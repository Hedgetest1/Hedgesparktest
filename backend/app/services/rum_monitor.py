"""
rum_monitor.py — Real-user monitoring (RUM) aggregation + regression detection.

Closes the merchant-observed perf gap that Lighthouse alone cannot: even
with the public-origin nightly run (see lighthouse_monitor.py), synthetic
probes miss real-user variance — slow 4G on mobile, cold-cache first
visit, different geos, extension interference. Those only show up in
samples collected from actual browsers loading the dashboard.

What this module does
---------------------
1. Accept per-sample web-vitals ingestion: TTFB, FCP, LCP, CLS, INP.
   (See app/api/rum.py for the POST /rum/metric endpoint.)
2. Keep a rolling per-(route, metric) sample window in Redis — last 500
   samples, no per-user identity retained.
3. Compute p75 on demand from the rolling window, store a daily p75
   snapshot, keep 14 days of history.
4. Once per day (gated inside the service, invoked from aggregation
   worker), compare today's p75 vs 7-day median p75 and emit a
   `rum_regression` ops_alert per regressed (route, metric). This is
   the ALERT class that fires when merchants started feeling the
   dashboard as slower without the app code changing.

Scale
-----
At 10k merchants each loading the dashboard once per day on ~5 routes,
~50k samples/day. Redis stores only the 500 newest per (route, metric)
so total footprint is 5 routes × 5 metrics × 500 samples × ~16 bytes
≈ 200 KB. Trivial.

Budget
------
Zero-LLM. Deterministic quantile math only. Rate limits live at the
ingestion endpoint.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("rum_monitor")


# Metrics we accept. Everything else is rejected at the endpoint layer.
ALLOWED_METRICS: tuple[str, ...] = ("ttfb", "fcp", "lcp", "cls", "inp")

# Upper bounds per metric — reject anything beyond these as a bad client.
# Values in milliseconds except CLS (unitless 0-1 score).
_METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "ttfb": (0.0, 60_000.0),      # TTFB beyond 60s is a timeout, not perf data
    "fcp": (0.0, 60_000.0),
    "lcp": (0.0, 60_000.0),
    "inp": (0.0, 60_000.0),
    "cls": (0.0, 10.0),           # CLS is 0..infinite in theory; realistically < 10
}

# Regression thresholds — mirror lighthouse_monitor for consistency.
# The percentage threshold catches creeping drift; the absolute threshold
# avoids firing on noise when baseline is tiny.
_TIME_METRIC_REGRESSION_PCT = 0.20
_TIME_METRIC_REGRESSION_ABS_MS: dict[str, float] = {
    "ttfb": 250.0,
    "fcp": 300.0,
    "lcp": 300.0,
    "inp": 50.0,
}
_CLS_REGRESSION_ABS = 0.05

# Redis keys
_SAMPLES_KEY = "hs:rum:samples:{route}:{metric}"    # LIST, last 500 values
_SAMPLES_CAP = 500
_SAMPLES_TTL_SECONDS = 7 * 86400                     # 7d — samples churn fast
_P75_HIST_KEY = "hs:rum:p75_hist:{route}:{metric}"  # LIST of daily p75 dicts
_P75_HIST_MAX = 14                                   # 14-day rolling window
_P75_HIST_TTL_SECONDS = 30 * 86400
_DAILY_GATE_KEY = "hs:rum:last_run:{date}"
_DAILY_GATE_TTL = 30 * 3600                          # 30h anti-double-fire

# Route allowlist — samples with unknown routes are accepted but bucketed
# under `/__unknown` so one mis-tagged bucket doesn't poison per-route
# history. Regression detector iterates whatever routes are present in
# Redis — no static config needed.
_ROUTE_MAX_LEN = 128


def _safe_route(route: str | None) -> str:
    if not route:
        return "/__unknown"
    route = route.strip()[:_ROUTE_MAX_LEN] or "/__unknown"
    # Strip query/hash; ops triage is per-path, not per-full-URL.
    for sep in ("?", "#"):
        idx = route.find(sep)
        if idx >= 0:
            route = route[:idx]
    if not route.startswith("/"):
        route = "/" + route
    return route[:_ROUTE_MAX_LEN]


def ingest_sample(rc, route: str | None, metric: str, value: float) -> bool:
    """Push one sample into the per-(route, metric) rolling list.

    Returns True when stored, False when rejected (unknown metric, out
    of bounds, redis down). Never raises — ingestion is fire-and-forget.
    """
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("rum_monitor.ingest.redis_down")
        return False
    if metric not in ALLOWED_METRICS:
        return False
    lo, hi = _METRIC_BOUNDS[metric]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if not (lo <= v <= hi):
        return False
    safe_route = _safe_route(route)
    try:
        key = _SAMPLES_KEY.format(route=safe_route, metric=metric)
        pipe = rc.pipeline()
        pipe.lpush(key, f"{v:.6f}")
        pipe.ltrim(key, 0, _SAMPLES_CAP - 1)
        pipe.expire(key, _SAMPLES_TTL_SECONDS)
        pipe.execute()
        return True
    except Exception as exc:
        log.warning("rum: ingest failed route=%s metric=%s: %s", safe_route, metric, exc)
        return False


def compute_p75(rc, route: str, metric: str) -> float | None:
    """Compute the 75th-percentile of the current sample window.
    Returns None when fewer than 20 samples (too noisy to trust)."""
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("rum_monitor.p75.redis_down")
        return None
    try:
        key = _SAMPLES_KEY.format(route=_safe_route(route), metric=metric)
        raw = rc.lrange(key, 0, _SAMPLES_CAP - 1) or []
        values: list[float] = []
        for entry in raw:
            try:
                s = entry.decode("utf-8") if isinstance(entry, bytes) else str(entry)
                values.append(float(s))
            except Exception:
                continue
        if len(values) < 20:
            return None
        values.sort()
        # statistics.quantiles n=4 → [p25, p50, p75]
        return statistics.quantiles(values, n=4)[2]
    except Exception as exc:
        log.warning("rum: p75 compute failed route=%s metric=%s: %s", route, metric, exc)
        return None


def _known_pairs(rc) -> list[tuple[str, str]]:
    """Discover every (route, metric) pair with live samples. Used by the
    daily regression job so we don't hardcode a route list."""
    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("rum_monitor.known_pairs.redis_down")
        return []
    pairs: list[tuple[str, str]] = []
    try:
        cursor = 0
        while True:
            cursor, keys = rc.scan(cursor=cursor, match="hs:rum:samples:*", count=200)
            for raw in keys or []:
                k = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                # Format: hs:rum:samples:{route}:{metric}
                # route may itself contain ':' but we split from the right.
                try:
                    _, _, rest = k.partition("hs:rum:samples:")
                    route, _, metric = rest.rpartition(":")
                    if route and metric in ALLOWED_METRICS:
                        pairs.append((route, metric))
                except Exception:
                    continue
            if cursor == 0:
                break
    except Exception as exc:
        log.warning("rum: scan failed: %s", exc)
    return pairs


def _append_p75_history(rc, route: str, metric: str, p75: float, captured_at: str) -> None:
    try:
        key = _P75_HIST_KEY.format(route=_safe_route(route), metric=metric)
        entry = json.dumps({"captured_at": captured_at, "p75": float(p75)}, default=str)
        pipe = rc.pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, _P75_HIST_MAX - 1)
        pipe.expire(key, _P75_HIST_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        log.warning("rum: p75 history append failed: %s", exc)


def _load_p75_history(rc, route: str, metric: str) -> list[dict]:
    try:
        key = _P75_HIST_KEY.format(route=_safe_route(route), metric=metric)
        raw_entries = rc.lrange(key, 0, _P75_HIST_MAX - 1) or []
        out: list[dict] = []
        for raw in raw_entries:
            try:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                out.append(json.loads(s))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _regression_threshold(metric: str) -> tuple[float | None, float | None]:
    """Return (pct_threshold, abs_threshold). CLS has no pct threshold."""
    if metric == "cls":
        return (None, _CLS_REGRESSION_ABS)
    return (_TIME_METRIC_REGRESSION_PCT, _TIME_METRIC_REGRESSION_ABS_MS.get(metric, 300.0))


def _check_p75_regression(today_p75: float, history: list[dict], metric: str) -> dict | None:
    """Compare today's p75 vs 7-day median of historical p75. Returns a
    regression dict or None if nothing regressed or baseline too sparse."""
    vals: list[float] = []
    for h in history[1:]:  # skip idx 0 (today's snapshot we just pushed)
        try:
            v = h.get("p75")
            if v is not None:
                vals.append(float(v))
        except Exception:
            continue
    if len(vals) < 3:
        return None
    baseline = statistics.median(vals)
    if baseline <= 0:
        return None
    delta = today_p75 - baseline
    pct_t, abs_t = _regression_threshold(metric)
    if metric == "cls":
        if delta >= (abs_t or _CLS_REGRESSION_ABS):
            return {
                "metric": metric,
                "current": round(today_p75, 3),
                "baseline": round(baseline, 3),
                "delta": round(delta, 3),
                "unit": "score",
                "threshold_abs": abs_t,
            }
        return None
    pct = delta / baseline
    if pct_t is None:
        return None
    if pct >= pct_t and delta >= (abs_t or 0):
        return {
            "metric": metric,
            "current": round(today_p75, 1),
            "baseline": round(baseline, 1),
            "delta": round(delta, 1),
            "pct": round(pct, 3),
            "unit": "ms",
            "threshold_pct": pct_t,
            "threshold_abs": abs_t,
        }
    return None


def _in_daily_window(now: datetime) -> bool:
    # Same 02-04 UTC window as Lighthouse — this is the low-traffic
    # quiet period for synthetic + aggregate work.
    return 2 <= now.hour < 4


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
        record_silent_return("rum_monitor.mark_ran.exception")


def _emit_regression_alert(db: Session, route: str, findings: list[dict]) -> int:
    """One alert per regressed route (collapses multiple metric regressions
    into a single row)."""
    if not findings:
        return 0
    from app.services.alerting import write_alert
    pieces = [
        f"{r['metric']}: {r['current']}{r['unit']} "
        f"(baseline {r['baseline']}{r['unit']}, +{r['delta']}{r['unit']}"
        + (f", +{int(r['pct']*100)}%" if "pct" in r else "") + ")"
        for r in findings
    ]
    try:
        # heal-detection: daily gate via _DAILY_GATE_KEY — one alert per route per day; recovery = next day's RUM data within budget
        write_alert(
            db,
            severity="warning",
            source=f"rum:{route}"[:64],
            alert_type="rum_regression",
            summary=(
                f"RUM p75 regression on {route} vs 7-day median: " + " · ".join(pieces)
            ),
            detail={"route": route, "regressions": findings},
        )
        return 1
    except Exception as exc:
        log.warning("rum: alert write failed route=%s: %s", route, exc)
        return 0


def run_daily_regression_check(db: Session, *, force: bool = False) -> dict:
    """Entry point invoked from aggregation_worker once per day.

    Walks every (route, metric) with live samples, computes today's p75,
    appends to the rolling history, detects regression vs 7-day median.
    One `rum_regression` alert per regressed route (not per metric — ops
    triage is already noisy).

    With force=True both the window gate and the daily dedup gate are
    bypassed — used for manual re-runs and tests.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    date_key = now.strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        rc = None

    if rc is None:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("rum_monitor.daily_check.redis_down")
        return {"ran": False, "reason": "redis_down"}

    if not force:
        if not _in_daily_window(now):
            return {"ran": False, "reason": "outside_window"}
        if _already_ran_today(rc, date_key):
            return {"ran": False, "reason": "already_ran"}

    per_route_findings: dict[str, list[dict]] = {}
    alerts_fired = 0
    routes_checked = 0
    pairs = _known_pairs(rc)

    # Group by route so we emit one alert per route.
    by_route: dict[str, list[tuple[str, float]]] = {}
    for route, metric in pairs:
        p75 = compute_p75(rc, route, metric)
        if p75 is None:
            continue
        by_route.setdefault(route, []).append((metric, p75))

    for route, metric_p75s in by_route.items():
        routes_checked += 1
        findings: list[dict] = []
        for metric, p75 in metric_p75s:
            history = _load_p75_history(rc, route, metric)
            history_with_today = (
                [{"captured_at": now.isoformat(), "p75": p75}] + history
            )
            reg = _check_p75_regression(p75, history_with_today, metric)
            if reg is not None:
                findings.append(reg)
            _append_p75_history(rc, route, metric, p75, now.isoformat())
        if findings:
            per_route_findings[route] = findings
            alerts_fired += _emit_regression_alert(db, route, findings)

    _mark_ran_today(rc, date_key)

    log.info(
        "rum: ran date=%s routes=%d alerts=%d",
        date_key, routes_checked, alerts_fired,
    )
    return {
        "ran": True,
        "status": "ok",
        "routes_checked": routes_checked,
        "alerts_fired": alerts_fired,
        "per_route": per_route_findings,
    }
