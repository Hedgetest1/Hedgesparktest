"""
lighthouse_monitor.py — Nightly Lighthouse regression watchdog.

Closes the A3 coverage gap: dashboard performance drift went unnoticed
because the existing `dashboard/scripts/run_lighthouse.mjs` is a
preflight hard-gate (opt-in, runs on commit) — it catches budget
violations but NEVER flags a slow creep. An LCP that crept from
1.2s to 2.4s over two weeks sits under the 3s budget threshold and
ships silently.

This module adds:
  1. Once-per-day run of Lighthouse in --json mode against localhost:3000.
  2. Per-route Core Web Vitals snapshots stored in Redis (14-day rolling).
  3. Regression detection: compare today's metrics vs 7-day median,
     alert when LCP/TBT regress > 20% AND absolute delta > 300 ms,
     OR CLS regresses > 0.05 absolute.
  4. `lighthouse_regression` ops_alert per regressed route.

All zero-cost — runs against the existing dashboard on loopback.
Scheduled via aggregation_worker guard (once per day, 02:00-04:00 UTC
window) rather than a new pm2 process (keeps ecosystem.config.js
untouched).
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("lighthouse_monitor")

_LH_SCRIPT = "/opt/wishspark/dashboard/scripts/run_lighthouse.mjs"

# Target URL — defaults to loopback, overridable via LH_BASE_URL env.
#
# SCOPE LIMITATION (honest disclosure):
#   Loopback Lighthouse measures SERVER-LOCAL render — same box as the
#   backend, no Traefik TLS, no CDN, no merchant network path. The
#   numbers we collect are the UPPER BOUND of what a merchant could
#   see (best case). A regression here indicates the APP code got
#   slower; a regression merchants actually feel might ALSO include
#   network/CDN/TLS regressions we won't catch from here.
#
# To measure merchant-observed performance, set LH_BASE_URL to the
# public domain (e.g. https://app.hedgesparkhq.com). Trade-off: the
# public run is subject to staging/prod traffic during the run window
# and the 02-04 UTC gate may coincide with CDN cache warmth variance.
# We keep loopback as default because:
#   1. Consistency — same environment run-over-run, trend is honest
#   2. Zero external dependency — no DNS, no CDN, no TLS cert renewal
#   3. Regression signal is strictly about code, which is what we own
#
# What we DON'T catch (accepted):
#   - Traefik routing latency
#   - Let's Encrypt cert handshake costs
#   - Cloudflare (once wired) CDN perf
#   - Real-user network conditions
#   These belong in a separate RUM (real-user monitoring) layer, not
#   this detector. Documented in project_brutal_scoring_rubric.md.
_LH_BASE_URL = os.getenv("LH_BASE_URL", "http://127.0.0.1:3000")
_LH_TIMEOUT_SECONDS = 240  # Lighthouse is slow; 4-min budget per full run

# Regression thresholds. Every number has a reason:
#  - LCP pct 20% — below this is noise from CPU / network jitter in headless
#  - LCP abs 300ms — ensures we don't alert on 50ms→60ms drift
#  - TBT pct 30% — TBT is noisier than LCP so higher band
#  - CLS 0.05 abs — CLS is a unitless 0-1 score; 0.05 is meaningful regression
_LCP_REGRESSION_PCT = 0.20
_LCP_REGRESSION_ABS_MS = 300
_TBT_REGRESSION_PCT = 0.30
_TBT_REGRESSION_ABS_MS = 150
_CLS_REGRESSION_ABS = 0.05

# Rolling window + schedule
_SNAPSHOT_TTL_SECONDS = 14 * 86400      # keep 14 days of history
_DAILY_GATE_KEY = "hs:lighthouse:last_run:{date}"
_DAILY_GATE_TTL = 30 * 3600              # 30h — guards against double-fire
_ROUTE_HISTORY_KEY = "hs:lighthouse:hist:{route}"
_HISTORY_MAX_ENTRIES = 14                 # one per day

# Schedule window (UTC). aggregation_worker runs every 5 min so we gate
# to a 2h window once a day. 02:00-04:00 UTC = 03:00-05:00 Rome summer.
_SCHEDULE_WINDOW_START_HOUR_UTC = 2
_SCHEDULE_WINDOW_END_HOUR_UTC = 4


def _in_schedule_window(now: datetime) -> bool:
    return _SCHEDULE_WINDOW_START_HOUR_UTC <= now.hour < _SCHEDULE_WINDOW_END_HOUR_UTC


def _already_ran_today(rc, date_key: str) -> bool:
    try:
        return bool(rc.get(_DAILY_GATE_KEY.format(date=date_key)))
    except Exception:
        return False


def _mark_ran_today(rc, date_key: str) -> None:
    try:
        rc.setex(_DAILY_GATE_KEY.format(date=date_key), _DAILY_GATE_TTL, "1")
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("lighthouse_monitor.mark_ran.exception")


def _run_lighthouse_subprocess() -> dict | None:
    """Invoke the Node Lighthouse script in --json mode. Returns parsed
    JSON or None on failure. Never raises."""
    try:
        proc = subprocess.run(
            ["node", _LH_SCRIPT, "--json", "--url", _LH_BASE_URL],
            capture_output=True,
            timeout=_LH_TIMEOUT_SECONDS,
            text=True,
        )
    except subprocess.TimeoutExpired:
        log.warning("lighthouse: subprocess timed out after %ds", _LH_TIMEOUT_SECONDS)
        return None
    except FileNotFoundError:
        log.warning("lighthouse: node binary not found in PATH")
        return None
    except Exception as exc:
        log.warning("lighthouse: subprocess launch failed: %s", exc)
        return None

    if proc.returncode != 0:
        log.warning(
            "lighthouse: subprocess exited %d stderr=%s",
            proc.returncode, (proc.stderr or "")[:500],
        )
        return None

    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("lighthouse: JSON parse failed: %s | stdout head: %s",
                    exc, (proc.stdout or "")[:300])
        return None


def _append_history(rc, route: str, metrics: dict, captured_at: str) -> None:
    """Push today's metrics into a per-route Redis list, cap at
    _HISTORY_MAX_ENTRIES, set TTL. Never raises."""
    try:
        key = _ROUTE_HISTORY_KEY.format(route=route)
        entry = json.dumps({
            "captured_at": captured_at,
            "metrics": metrics,
        }, default=str)
        pipe = rc.pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, _HISTORY_MAX_ENTRIES - 1)
        pipe.expire(key, _SNAPSHOT_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        log.warning("lighthouse: history append failed route=%s: %s", route, exc)


def _load_history(rc, route: str) -> list[dict]:
    """Pull the route's rolling history. Returns [] on any failure."""
    try:
        key = _ROUTE_HISTORY_KEY.format(route=route)
        raw_entries = rc.lrange(key, 0, _HISTORY_MAX_ENTRIES - 1) or []
        out = []
        for raw in raw_entries:
            try:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                out.append(json.loads(s))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _compute_baseline(history: list[dict], field: str) -> float | None:
    """Median of `field` across the last N days (excluding today —
    which is prepended to the list first but passed in as excludeable
    here). Returns None when < 3 observations (too sparse)."""
    vals = []
    for h in history[1:]:  # skip index 0 (today)
        try:
            v = h.get("metrics", {}).get(field)
            if v is not None:
                vals.append(float(v))
        except Exception:
            continue
    if len(vals) < 3:
        return None
    return statistics.median(vals)


def _check_regression(today_metrics: dict, history: list[dict]) -> list[dict]:
    """Return a list of regression dicts (one per metric that regressed).
    Empty list when nothing regresses OR baseline is too sparse."""
    findings = []
    for field, pct_t, abs_t, unit in (
        ("lcp_ms", _LCP_REGRESSION_PCT, _LCP_REGRESSION_ABS_MS, "ms"),
        ("tbt_ms", _TBT_REGRESSION_PCT, _TBT_REGRESSION_ABS_MS, "ms"),
    ):
        current = today_metrics.get(field)
        if current is None:
            continue
        baseline = _compute_baseline(history, field)
        if baseline is None or baseline <= 0:
            continue
        delta = current - baseline
        pct = delta / baseline
        if pct >= pct_t and delta >= abs_t:
            findings.append({
                "metric": field,
                "current": round(float(current), 1),
                "baseline": round(float(baseline), 1),
                "delta": round(float(delta), 1),
                "pct": round(float(pct), 3),
                "unit": unit,
                "threshold_pct": pct_t,
                "threshold_abs": abs_t,
            })

    # CLS uses absolute-only threshold (it's a 0-1 score, not ms)
    current_cls = today_metrics.get("cls")
    if current_cls is not None:
        baseline_cls = _compute_baseline(history, "cls")
        if baseline_cls is not None:
            delta = float(current_cls) - float(baseline_cls)
            if delta >= _CLS_REGRESSION_ABS:
                findings.append({
                    "metric": "cls",
                    "current": round(float(current_cls), 3),
                    "baseline": round(float(baseline_cls), 3),
                    "delta": round(float(delta), 3),
                    "unit": "score",
                    "threshold_abs": _CLS_REGRESSION_ABS,
                })
    return findings


def _emit_alerts(db: Session, route: str, regressions: list[dict]) -> int:
    """Emit one lighthouse_regression alert per regressed route (collapses
    all metrics into a single alert per route)."""
    if not regressions:
        return 0
    from app.services.alerting import write_alert
    pieces = [
        f"{r['metric']}: {r['current']}{r['unit']} "
        f"(baseline {r['baseline']}{r['unit']}, "
        f"+{r['delta']}{r['unit']}"
        + (f", +{int(r['pct']*100)}%" if 'pct' in r else "") + ")"
        for r in regressions
    ]
    try:
        write_alert(
            db,
            severity="warning",
            source=f"lighthouse:{route}"[:64],
            alert_type="lighthouse_regression",
            summary=(
                f"Lighthouse regression on {route} vs 7-day median: "
                + " · ".join(pieces)
            ),
            detail={
                "route": route,
                "regressions": regressions,
            },
        )
        return 1
    except Exception as exc:
        log.warning("lighthouse: alert write failed route=%s: %s", route, exc)
        return 0


def run_nightly_check(db: Session, *, force: bool = False) -> dict:
    """Entry point invoked from aggregation_worker once per day.

    With force=True the daily gate is bypassed — used for manual
    re-runs via ops endpoint or tests.

    Returns a summary dict: {ran, routes, regressions, alerts_fired}.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    date_key = now.strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        rc = None

    # Gate: only in-window + only once per day (unless force)
    if not force:
        if not _in_schedule_window(now):
            return {"ran": False, "reason": "outside_window"}
        if rc is not None and _already_ran_today(rc, date_key):
            return {"ran": False, "reason": "already_ran"}

    t0 = time.time()
    result = _run_lighthouse_subprocess()
    elapsed = round(time.time() - t0, 1)

    if result is None:
        # Emit an ops_alert so ops can see when the nightly run FAILS —
        # a silently-broken monitor is worse than no monitor.
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source="lighthouse_monitor",
                alert_type="lighthouse_run_failed",
                summary=f"Lighthouse nightly run failed after {elapsed}s",
                detail={"elapsed_seconds": elapsed, "base_url": _LH_BASE_URL},
            )
        except Exception:
            pass  # SILENT-EXCEPT-OK: alerting failure already logged by alerting.py
        if rc is not None:
            _mark_ran_today(rc, date_key)  # don't retry in-cycle storms
        return {"ran": True, "status": "subprocess_failed", "elapsed_seconds": elapsed}

    # Success path: store snapshots + detect regressions
    routes = result.get("routes", []) or []
    alerts_fired = 0
    total_regressions = 0
    per_route_findings: dict[str, list[dict]] = {}

    for r in routes:
        route = r.get("route")
        metrics = r.get("metrics") or {}
        if not route:
            continue
        history = _load_history(rc, route) if rc is not None else []
        # Prepend today's capture so the list starts with "today" at idx 0.
        history_with_today = [{"captured_at": now.isoformat(), "metrics": metrics}] + history
        regressions = _check_regression(metrics, history_with_today)
        if regressions:
            per_route_findings[route] = regressions
            alerts_fired += _emit_alerts(db, route, regressions)
            total_regressions += len(regressions)
        # Append AFTER regression check so today doesn't contaminate baseline
        if rc is not None:
            _append_history(rc, route, metrics, now.isoformat())

    if rc is not None:
        _mark_ran_today(rc, date_key)

    log.info(
        "lighthouse: ran in %.1fs routes=%d regressions=%d alerts=%d",
        elapsed, len(routes), total_regressions, alerts_fired,
    )
    return {
        "ran": True,
        "status": "ok",
        "elapsed_seconds": elapsed,
        "routes": len(routes),
        "regressions": total_regressions,
        "alerts_fired": alerts_fired,
        "per_route": per_route_findings,
    }
