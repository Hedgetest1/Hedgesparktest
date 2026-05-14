"""
slo.py — Service Level Objectives + live latency observability.

Records per-route request timing in Redis with rolling windows. Computes
p50/p95/p99 on demand. Exposes an SLO definition catalogue and evaluates
error-budget burn so ops sees WHAT to act on, not just "slow".

Design notes
------------
- Zero dependencies: Redis ZSETs only. If Redis is down, everything
  fails open (the middleware never raises).
- Two windows per route: 5-minute and 60-minute. Quantiles computed
  client-side on ingest — no ranged scripts, no Lua.
- Sampling: every request. Footprint is ~24 bytes per observation.
  At 100 req/s that's ~86MB per day per route; we TTL hot windows to
  2 hours so the footprint is bounded regardless of scale.

Public API
----------
    record_timing(route, method, status, duration_ms) -> None
    route_stats(route, window="5m") -> dict
    slo_report() -> list[dict]  (per-SLO error budget state)
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass

from app.core.silent_fallback import record_silent_return

log = logging.getLogger("slo")

_TTL_SECONDS = 2 * 3600  # 2 hours — keeps memory bounded

# Windows we track for every route. Key format:
#   hs:slo:tm:{window}:{route}   — ZSET of (timestamp.ms, duration_ms)
#   hs:slo:count:{window}:{route}:ok|err
_WINDOWS = {
    "5m": 300,
    "60m": 3600,
}

# --- Batched-flush record path -------------------------------------------
#
# Pre-2026-05-14 every request issued 2 Redis pipelines (one per window),
# each with 5 ops (zadd + zremrangebyscore + expire + incr + expire) =
# 10 Redis ops in the synchronous request path. Under 1000 concurrent
# merchants, the cumulative round-trip + hot-ZSET serialisation pushed
# /dashboard/overview from 30ms baseline to 80ms+ p95 and capped the
# backend throughput at ~70 req/s — the proven bottleneck found in the
# external-CTO load-test exploration.
#
# Fix: append to an in-process buffer; a background thread flushes every
# 1s OR when the buffer hits 200 entries. The per-request cost drops to
# a list append + lock check (~microsecond). Redis traffic collapses to
# 1 pipeline per second per worker instead of 10 ops per request.
#
# Trade-offs:
#   * Up to 1s of observation lag (acceptable: SLO is observability,
#     not a load-bearing real-time metric).
#   * Buffer is lost on worker restart (~200 obs lost max; PM2 reload
#     drains gracefully — see _drain_at_exit below).
#   * `zremrangebyscore` moves from per-request to per-flush: cuts ~50%
#     of Redis work since one flush trims all routes once.
#   * Set SLO_BATCH_FLUSH=0 to revert to the per-request pipeline
#     pattern (escape hatch for diagnosis or in case the batched path
#     hides a real issue).

_BATCH_FLUSH_ENABLED = os.getenv("SLO_BATCH_FLUSH", "1") != "0"
_FLUSH_INTERVAL_SEC = 1.0
_FLUSH_BATCH_SIZE = 200

# Buffer entry: (route, method, status, duration_ms, now_ns)
# multi-worker: accept-degrade — per-uvicorn-worker buffer is intentional.
# Each worker holds its own observations and flushes to Redis every 1s.
# Cross-process aggregation happens in Redis (shared) — the in-process
# buffer is just the per-worker accumulator. Losing one worker's
# unflushed buffer on crash drops ~1s of observations from that worker,
# which is acceptable for SLO telemetry (observability, not load-bearing).
_BUFFER: list[tuple[str, str, int, float, int]] = []
_BUFFER_LOCK = threading.Lock()  # multi-worker: thread-only
_FLUSH_LOCK = threading.Lock()  # multi-worker: thread-only
_LAST_FLUSH_MONO = [time.monotonic()]
_BG_FLUSHER_STARTED = [False]


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _start_background_flusher() -> None:
    """Idempotent — starts a daemon thread that flushes every
    _FLUSH_INTERVAL_SEC. First record_timing call kicks it off per
    worker process. Daemon means it dies with the worker (no graceful
    shutdown waste); the atexit hook drains the residual buffer."""
    if _BG_FLUSHER_STARTED[0]:
        return
    _BG_FLUSHER_STARTED[0] = True

    def _loop() -> None:
        while True:
            try:
                time.sleep(_FLUSH_INTERVAL_SEC)
                _flush_buffer()
            except Exception as exc:
                log.debug("slo: background flusher tick failed: %s", exc)

    t = threading.Thread(target=_loop, name="slo-flusher", daemon=True)
    t.start()

    import atexit
    atexit.register(_flush_buffer)


def _flush_buffer() -> None:
    """Drain the in-process buffer and write to Redis in one pipeline.
    Single-flusher per worker via _FLUSH_LOCK (no thundering herd if
    multiple opportunistic flushes race)."""
    if not _FLUSH_LOCK.acquire(blocking=False):
        return  # another flush already in-flight; let it drain
    try:
        with _BUFFER_LOCK:
            if not _BUFFER:
                _LAST_FLUSH_MONO[0] = time.monotonic()
                return
            batch = _BUFFER[:]
            _BUFFER.clear()
            _LAST_FLUSH_MONO[0] = time.monotonic()

        rc = _redis()
        if rc is None:
            record_silent_return("slo.flush_redis_down")
            return

        # Group by (window, method, route) so we issue ONE
        # zremrangebyscore per route+window per flush instead of per-
        # observation. Counts batched via Counter for the same shape.
        from collections import defaultdict
        latest_ts_ms: dict[tuple[str, str, str], int] = {}
        members_to_add: dict[tuple[str, str, str], dict[str, int]] = defaultdict(dict)
        ok_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        err_counts: dict[tuple[str, str, str], int] = defaultdict(int)

        for route, method, status, dur_ms, now_ns in batch:
            now_ms = now_ns // 1_000_000
            ok = 200 <= status < 500
            for window_name in _WINDOWS:
                grp = (window_name, method, route)
                latest_ts_ms[grp] = max(latest_ts_ms.get(grp, 0), now_ms)
                members_to_add[grp][f"{now_ns}:{dur_ms:.2f}"] = now_ms
                if ok:
                    ok_counts[grp] += 1
                else:
                    err_counts[grp] += 1

        try:
            pipe = rc.pipeline()
            for (window_name, method, route), members in members_to_add.items():
                key_tm = f"hs:slo:tm:{window_name}:{method}:{route}"
                key_ok = f"hs:slo:ok:{window_name}:{method}:{route}"
                key_err = f"hs:slo:err:{window_name}:{method}:{route}"
                pipe.zadd(key_tm, members)
                # One zremrangebyscore per (route, window) per flush —
                # bounded by the latest observed timestamp.
                cutoff_ms = latest_ts_ms[(window_name, method, route)] - (
                    _WINDOWS[window_name] * 1000
                )
                pipe.zremrangebyscore(key_tm, 0, cutoff_ms)
                pipe.expire(key_tm, _TTL_SECONDS)
                ok_n = ok_counts[(window_name, method, route)]
                err_n = err_counts[(window_name, method, route)]
                if ok_n:
                    pipe.incrby(key_ok, ok_n)
                    pipe.expire(key_ok, _TTL_SECONDS)
                if err_n:
                    pipe.incrby(key_err, err_n)
                    pipe.expire(key_err, _TTL_SECONDS)
            pipe.execute()
        except Exception as exc:
            log.debug("slo: flush pipeline failed: %s", exc)
    finally:
        _FLUSH_LOCK.release()


def record_timing(route: str, method: str, status: int, duration_ms: float) -> None:
    """Log one request observation. Never raises.

    Default path (2026-05-14): append to in-process buffer; the
    background daemon owns flushing exclusively (every 1s). The per-
    request cost is one list append + a microsecond lock acquire. No
    thread spawn on the request path — earlier opportunistic spawn
    pattern caused GIL/context-switch contention under 1000+
    concurrent requests and regressed p95 from 80ms → 11s.

    Legacy per-request pipeline path is reachable via
    SLO_BATCH_FLUSH=0 for diagnosis. Same Redis schema either way,
    so readers (route_stats / slo_report) are oblivious.

    Member key uses nanosecond precision so observations in the same
    millisecond do not coalesce into one ZSET entry. Score stays in
    milliseconds to keep `zremrangebyscore` windowing cheap.
    """
    if not _BATCH_FLUSH_ENABLED:
        _record_timing_legacy(route, method, status, duration_ms)
        return

    _start_background_flusher()
    now_ns = time.time_ns()
    with _BUFFER_LOCK:
        # Cap buffer size to bound memory if Redis is unreachable for
        # long stretches (background flusher silently drops old data,
        # but a runaway buffer would still eat heap). 5× the batch
        # size = generous headroom for a temporary Redis outage.
        if len(_BUFFER) >= _FLUSH_BATCH_SIZE * 5:
            # Drop oldest half — keep most recent observations
            del _BUFFER[: _FLUSH_BATCH_SIZE * 2]
        _BUFFER.append((route, method, status, duration_ms, now_ns))


def _record_timing_legacy(
    route: str, method: str, status: int, duration_ms: float,
) -> None:
    """Pre-2026-05-14 per-request pipeline path. Kept for
    SLO_BATCH_FLUSH=0 diagnosis (e.g., to bisect if a future change
    breaks the batched path). Identical Redis writes."""
    rc = _redis()
    if rc is None:
        record_silent_return("slo.record")
        return
    try:
        now_ns = time.time_ns()
        now_ms = now_ns // 1_000_000
        ok = 200 <= status < 500
        for window_name, window_seconds in _WINDOWS.items():
            cutoff_ms = now_ms - (window_seconds * 1000)
            key_tm = f"hs:slo:tm:{window_name}:{method}:{route}"
            key_ok = f"hs:slo:ok:{window_name}:{method}:{route}"
            key_err = f"hs:slo:err:{window_name}:{method}:{route}"
            pipe = rc.pipeline()
            pipe.zadd(key_tm, {f"{now_ns}:{duration_ms:.2f}": now_ms})
            pipe.zremrangebyscore(key_tm, 0, cutoff_ms)
            pipe.expire(key_tm, _TTL_SECONDS)
            pipe.incr(key_ok if ok else key_err)
            pipe.expire(key_ok if ok else key_err, _TTL_SECONDS)
            pipe.execute()
    except Exception as exc:
        log.debug("slo: record failed: %s", exc)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    idx = q * (len(s) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return s[int(idx)]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def route_stats(route: str, method: str = "GET", window: str = "5m") -> dict:
    """Return per-route latency + error rate for the selected window."""
    rc = _redis()
    if rc is None or window not in _WINDOWS:
        return {"route": route, "method": method, "window": window, "ok": 0, "err": 0, "p50": 0, "p95": 0, "p99": 0}
    try:
        key_tm = f"hs:slo:tm:{window}:{method}:{route}"
        key_ok = f"hs:slo:ok:{window}:{method}:{route}"
        key_err = f"hs:slo:err:{window}:{method}:{route}"
        members = rc.zrange(key_tm, 0, -1)
        durations: list[float] = []
        for m in members:
            if isinstance(m, bytes):
                m = m.decode()
            try:
                _, dur = m.split(":", 1)
                durations.append(float(dur))
            except ValueError:
                continue
        ok = int(rc.get(key_ok) or 0)
        err = int(rc.get(key_err) or 0)
        total = ok + err
        return {
            "route": route,
            "method": method,
            "window": window,
            "ok": ok,
            "err": err,
            "total": total,
            "error_rate_pct": round((err / total * 100) if total else 0, 2),
            "p50_ms": round(_quantile(durations, 0.50), 1),
            "p95_ms": round(_quantile(durations, 0.95), 1),
            "p99_ms": round(_quantile(durations, 0.99), 1),
            "observations": len(durations),
        }
    except Exception as exc:
        log.warning("slo: route_stats failed: %s", exc)
        return {"route": route, "window": window, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# SLO catalogue — critical-path routes with explicit targets
# ---------------------------------------------------------------------------
#
# Error budget math: 99.5% over 60m window → error budget is 0.5% of requests.
# Burn rate = current_error_rate / allowed_error_rate.
# burn_rate > 2 → warning; > 10 → critical.

@dataclass(frozen=True)
class SLO:
    name: str
    route: str
    method: str
    availability_target_pct: float  # e.g. 99.5
    latency_p95_target_ms: float    # e.g. 500


CATALOGUE: list[SLO] = [
    # Merchant-critical request paths (daily use). Availability targets
    # follow business criticality: anything the dashboard fetches every
    # page-load is 99.9%; anything Pro-only but non-blocking is 99.0.
    # Latency targets are P95 — chosen to match merchant perception
    # thresholds (<300ms = instant, <800ms = fast, <1500ms = slow-ok).
    # 2026-05-06 corrected: /pro/rars was renamed; the real route is
    # /analytics/revenue-at-risk (with deprecated alias /pro/revenue-
    # at-risk). The stale entry was producing insufficient_data
    # forever — masking actual RARS latency drift. Same class for
    # /webhooks/shopify which is only a prefix; real handler is
    # /webhooks/shopify/orders. Caught by test_catalogue_routes_
    # resolve_to_real_handlers preventer added in this commit.
    SLO("rars_lite",            "/analytics/revenue-at-risk",    "GET",  99.5, 800),
    SLO("rars_pro_legacy",      "/pro/revenue-at-risk",          "GET",  99.5, 800),
    SLO("pro_causal",           "/pro/causal/explain",           "GET",  99.0, 1500),
    SLO("pro_anomalies",        "/pro/anomalies/fusion",         "GET",  99.0, 1500),
    SLO("scale_night_shift",    "/scale/night-shift/latest",     "GET",  99.5, 800),
    SLO("track",                "/track",                        "POST", 99.9, 200),
    SLO("webhooks_orders",      "/webhooks/shopify/orders",      "POST", 99.9, 400),
    SLO("system_health",        "/system/health",                "GET",  99.9, 300),
    # Added 2026-04-18 (C-2): core dashboard load + high-traffic Pro
    # analytics endpoints. Each is rendered on /app every merchant
    # session; a degradation is immediately visible.
    SLO("dashboard_overview",   "/dashboard/overview",           "GET",  99.9, 500),
    SLO("dashboard_overview_pro","/dashboard/overview/pro",      "GET",  99.5, 800),
    SLO("merchant_session",     "/merchant/me",                  "GET",  99.9, 200),
    SLO("brief_today",          "/brief/today",                  "GET",  99.5, 600),
    SLO("pro_revenue_autopsy",  "/pro/revenue-autopsy",          "GET",  99.0, 1200),
    SLO("pro_risk_forecast",    "/pro/risk-forecast",            "GET",  99.0, 1200),
    SLO("pro_nudges_rank",      "/pro/nudges/rank",              "GET",  99.0, 500),
    # Added 2026-05-06 (C-2 extension): high-traffic dashboard +
    # orders + behavioral + retention surfaces. Each fired
    # p95_drift alerts in the 2026-05-06 morning probe — proxy for
    # high merchant traffic. SLO contracts here promote them from
    # passive observation (route_stats only) to explicit error-budget
    # tracking with burn-rate alerting.
    SLO("today_snapshot",       "/analytics/today-snapshot",     "GET",  99.5, 600),
    SLO("orders_summary",       "/orders/summary",               "GET",  99.5, 600),
    SLO("orders_daily_revenue", "/orders/daily-revenue",         "GET",  99.0, 800),
    SLO("visitor_intent",       "/analytics/visitor-intent-classification", "GET", 99.0, 1000),
    SLO("pro_cohorts_monthly",  "/pro/cohorts/monthly",          "GET",  99.0, 1200),
    SLO("actions_candidates_pro","/actions/candidates/pro",      "GET",  99.5, 800),
]


def slo_report() -> list[dict]:
    """Evaluate every SLO in the catalogue against the live 60m window."""
    out = []
    for slo in CATALOGUE:
        stats = route_stats(slo.route, method=slo.method, window="60m")
        availability = 100.0 - float(stats.get("error_rate_pct", 0))
        p95 = float(stats.get("p95_ms", 0))
        obs = int(stats.get("observations", 0) or 0)

        # Error budget burn — how many standard deviations over the allowed error rate
        allowed_err_pct = max(0.0001, 100.0 - slo.availability_target_pct)
        burn_rate = (float(stats.get("error_rate_pct", 0))) / allowed_err_pct if obs > 0 else 0

        # Min-observation floor bumped 2026-05-13 from 10 → 30 after
        # Agent audit surfaced that low-traffic pre-production routes
        # (rars_lite obs=28, visitor_intent obs=12) were firing
        # latency_warning on cold-start outliers. p95 stat-significance
        # at obs=10 is dominated by 1-2 cold-start outliers; obs=30 is
        # industry standard. Production-scale (10k merchants) hits 30
        # observations within a minute on any pro route — invariant
        # not weakened, just sampling-shaped to match traffic profile.
        if obs < 30:
            health = "insufficient_data"
        elif availability < slo.availability_target_pct - 1:
            health = "breach"
        elif burn_rate > 10:
            health = "critical_burn"
        elif burn_rate > 2:
            health = "warning_burn"
        elif p95 > slo.latency_p95_target_ms * 1.5:
            health = "latency_breach"
        elif p95 > slo.latency_p95_target_ms:
            health = "latency_warning"
        else:
            health = "healthy"

        out.append({
            "name": slo.name,
            "route": slo.route,
            "method": slo.method,
            "availability_pct": round(availability, 2),
            "availability_target_pct": slo.availability_target_pct,
            "p95_ms": p95,
            "p95_target_ms": slo.latency_p95_target_ms,
            "error_rate_pct": stats.get("error_rate_pct", 0),
            "burn_rate": round(burn_rate, 2),
            "observations": obs,
            "health": health,
        })
    return out
