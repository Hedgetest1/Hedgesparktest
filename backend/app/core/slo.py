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


def _redis():
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def record_timing(route: str, method: str, status: int, duration_ms: float) -> None:
    """Log one request observation. Never raises.

    Member key uses nanosecond precision so observations in the same
    millisecond do not coalesce into one ZSET entry. Score stays in
    milliseconds to keep `zremrangebyscore` windowing cheap. Without
    the nanosecond spread a hot route (10k+ merchants) or a tight
    test loop would collapse 20+ observations into 1 entry and trip
    the `obs < 10 → insufficient_data` guard in slo_report."""
    rc = _redis()
    if rc is None:
        record_silent_return("slo.record")
        return
    try:
        now_ns = time.time_ns()
        now_ms = now_ns // 1_000_000
        ok = 200 <= status < 500  # 4xx counts as ok for availability purposes
        for window_name, window_seconds in _WINDOWS.items():
            cutoff_ms = now_ms - (window_seconds * 1000)
            key_tm = f"hs:slo:tm:{window_name}:{method}:{route}"
            key_ok = f"hs:slo:ok:{window_name}:{method}:{route}"
            key_err = f"hs:slo:err:{window_name}:{method}:{route}"
            # Use pipe to avoid N round-trips
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
    SLO("pro_night_shift",      "/pro/night-shift/latest",       "GET",  99.5, 800),
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

        if obs < 10:
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
