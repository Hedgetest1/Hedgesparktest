"""
lighthouse_monitor.py — Nightly Lighthouse regression watchdog.

Closes the A3 coverage gap: dashboard performance drift went unnoticed
because the existing `dashboard/scripts/run_lighthouse.mjs` is a
preflight hard-gate (opt-in, runs on commit) — it catches budget
violations but NEVER flags a slow creep. An LCP that crept from
1.2s to 2.4s over two weeks sits under the 3s budget threshold and
ships silently.

This module adds:
  1. Daily run of Lighthouse in --json mode against one or more origins.
     By default only the LOCAL (loopback) origin runs — measures app
     code perf, not network/TLS/CDN. When LH_PUBLIC_ENABLED=1 the
     PUBLIC origin (https://app.hedgesparkhq.com by default) also runs,
     measuring merchant-observed performance end-to-end.
  2. Per-origin-per-route Core Web Vitals snapshots stored in Redis
     (14-day rolling). Local history keys preserve the legacy format
     so existing Redis state and tests stay compatible.
  3. Regression detection: compare today's metrics vs 7-day median,
     alert when LCP/TBT regress > 20% AND absolute delta > 300 ms,
     OR CLS regresses > 0.05 absolute.
  4. `lighthouse_regression` ops_alert per regressed route (local) or
     `lighthouse_regression_public` (public). Separate alert classes
     so ops can distinguish "app slowed down" from "CDN/network slowed
     down" in a glance.

All zero-cost for the local run. Public runs pay one HTTPS handshake
per route; cost is negligible and bounded to a once-per-day window.
Scheduled via aggregation_worker guard (once per day, 02:00-04:00 UTC
window) rather than a new pm2 process.
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

# Public origin — end-to-end merchant path (Traefik TLS + real DNS + any
# downstream CDN). Gated off by default; enabling requires ops confirmation
# that the dashboard has enough live traffic to survive a synthetic pull
# during the 02-04 UTC window without skewing quieter perf buckets.
_LH_PUBLIC_URL = os.getenv("LH_PUBLIC_URL", "https://app.hedgesparkhq.com")
_LH_PUBLIC_ENABLED = os.getenv("LH_PUBLIC_ENABLED", "0") == "1"

_LH_TIMEOUT_SECONDS = 240  # Lighthouse is slow; 4-min budget per full run


def _origins_to_run() -> list[tuple[str, str]]:
    """Return ordered (origin_label, base_url) pairs. Local always runs;
    public appends only when the operator has enabled it."""
    origins: list[tuple[str, str]] = [("local", _LH_BASE_URL)]
    if _LH_PUBLIC_ENABLED:
        origins.append(("public", _LH_PUBLIC_URL))
    return origins


def _history_key(origin: str, route: str) -> str:
    """Legacy local key preserved to keep existing Redis state + tests
    working. Public origin gets its own namespace."""
    if origin == "local":
        return _ROUTE_HISTORY_KEY.format(route=route)
    return f"hs:lighthouse:hist:{origin}:{route}"


def _gate_key(origin: str, date_key: str) -> str:
    if origin == "local":
        return _DAILY_GATE_KEY.format(date=date_key)
    return f"hs:lighthouse:last_run:{origin}:{date_key}"


def _alert_type_regression(origin: str) -> str:
    return "lighthouse_regression" if origin == "local" else f"lighthouse_regression_{origin}"


def _alert_type_run_failed(origin: str) -> str:
    return "lighthouse_run_failed" if origin == "local" else f"lighthouse_run_failed_{origin}"

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


def _already_ran_today(rc, date_key: str, *, origin: str = "local") -> bool:
    try:
        return bool(rc.get(_gate_key(origin, date_key)))
    except Exception:
        return False


def _mark_ran_today(rc, date_key: str, *, origin: str = "local") -> None:
    try:
        rc.setex(_gate_key(origin, date_key), _DAILY_GATE_TTL, "1")
    except Exception:
        from app.core.silent_fallback import record_silent_return
        record_silent_return("lighthouse_monitor.mark_ran.exception")


def _run_lighthouse_subprocess(base_url: str | None = None) -> dict | None:
    """Invoke the Node Lighthouse script in --json mode. Returns parsed
    JSON or None on failure. Never raises."""
    url = base_url or _LH_BASE_URL
    try:
        proc = subprocess.run(
            ["node", _LH_SCRIPT, "--json", "--url", url],
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


def _append_history(rc, route: str, metrics: dict, captured_at: str, *, origin: str = "local") -> None:
    """Push today's metrics into a per-route Redis list, cap at
    _HISTORY_MAX_ENTRIES, set TTL. Never raises."""
    try:
        key = _history_key(origin, route)
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
        log.warning("lighthouse: history append failed origin=%s route=%s: %s", origin, route, exc)


def _load_history(rc, route: str, *, origin: str = "local") -> list[dict]:
    """Pull the route's rolling history. Returns [] on any failure."""
    try:
        key = _history_key(origin, route)
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


def _emit_alerts(db: Session, route: str, regressions: list[dict], *, origin: str = "local") -> int:
    """Emit one lighthouse_regression alert per regressed route (collapses
    all metrics into a single alert per route). Origin-aware: public-path
    regressions land on `lighthouse_regression_public` so ops can split
    app-code drift from network/CDN drift in triage."""
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
    source_prefix = "lighthouse" if origin == "local" else f"lighthouse:{origin}"
    origin_note = "" if origin == "local" else f" [{origin}]"
    try:
        write_alert(
            db,
            severity="warning",
            source=f"{source_prefix}:{route}"[:64],
            alert_type=_alert_type_regression(origin),
            summary=(
                f"Lighthouse regression on {route}{origin_note} vs 7-day median: "
                + " · ".join(pieces)
            ),
            detail={
                "route": route,
                "origin": origin,
                "regressions": regressions,
            },
        )
        return 1
    except Exception as exc:
        log.warning("lighthouse: alert write failed origin=%s route=%s: %s", origin, route, exc)
        return 0


def _run_single_origin(
    db: Session,
    rc,
    origin: str,
    base_url: str,
    now: datetime,
    date_key: str,
    *,
    force: bool,
) -> dict:
    """Execute the nightly check against one origin (local or public).
    Gate, subprocess, regression detection, alert emission all scoped to
    this origin. Returns a per-origin result dict.

    The gate is per-origin so enabling public for the first time does
    not get suppressed by a local run that already happened today.
    """
    if not force and rc is not None and _already_ran_today(rc, date_key, origin=origin):
        return {"origin": origin, "ran": False, "reason": "already_ran"}

    t0 = time.time()
    result = _run_lighthouse_subprocess(base_url)
    elapsed = round(time.time() - t0, 1)

    if result is None:
        try:
            from app.services.alerting import write_alert
            write_alert(
                db,
                severity="warning",
                source=f"lighthouse_monitor:{origin}"[:64],
                alert_type=_alert_type_run_failed(origin),
                summary=f"Lighthouse nightly run failed [{origin}] after {elapsed}s",
                detail={"origin": origin, "elapsed_seconds": elapsed, "base_url": base_url},
            )
        except Exception:
            pass  # SILENT-EXCEPT-OK: alerting failure already logged by alerting.py
        if rc is not None:
            _mark_ran_today(rc, date_key, origin=origin)
        return {
            "origin": origin,
            "ran": True,
            "status": "subprocess_failed",
            "elapsed_seconds": elapsed,
        }

    routes = result.get("routes", []) or []
    alerts_fired = 0
    total_regressions = 0
    per_route_findings: dict[str, list[dict]] = {}

    for r in routes:
        route = r.get("route")
        metrics = r.get("metrics") or {}
        if not route:
            continue
        history = _load_history(rc, route, origin=origin) if rc is not None else []
        history_with_today = [{"captured_at": now.isoformat(), "metrics": metrics}] + history
        regressions = _check_regression(metrics, history_with_today)
        if regressions:
            per_route_findings[route] = regressions
            alerts_fired += _emit_alerts(db, route, regressions, origin=origin)
            total_regressions += len(regressions)
        if rc is not None:
            _append_history(rc, route, metrics, now.isoformat(), origin=origin)

    if rc is not None:
        _mark_ran_today(rc, date_key, origin=origin)

    log.info(
        "lighthouse[%s]: ran in %.1fs routes=%d regressions=%d alerts=%d base=%s",
        origin, elapsed, len(routes), total_regressions, alerts_fired, base_url,
    )
    return {
        "origin": origin,
        "ran": True,
        "status": "ok",
        "elapsed_seconds": elapsed,
        "routes": len(routes),
        "regressions": total_regressions,
        "alerts_fired": alerts_fired,
        "per_route": per_route_findings,
    }


def run_nightly_check(db: Session, *, force: bool = False) -> dict:
    """Entry point invoked from aggregation_worker once per day.

    Runs Lighthouse against every enabled origin (local always; public
    when LH_PUBLIC_ENABLED=1). Gate and regression detection are
    per-origin so ops can distinguish app-code drift from CDN/TLS drift.

    With force=True both the schedule window AND the daily gate are
    bypassed — used for manual re-runs via ops endpoint or tests.

    Returns a flat legacy-compatible summary for back-compat with
    existing ops + tests. The per-origin breakdown lives under
    `origins`. The top-level `status` reflects the LOCAL origin so
    older call sites and assertions continue to work unchanged.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    date_key = now.strftime("%Y-%m-%d")

    try:
        from app.core.redis_client import _client
        rc = _client()
    except Exception:
        rc = None

    if not force:
        if not _in_schedule_window(now):
            return {"ran": False, "reason": "outside_window"}
        # Early skip when EVERY enabled origin already ran today — saves
        # a noop iteration. Per-origin gate inside _run_single_origin
        # handles the mixed case (local done, public pending).
        if rc is not None and all(
            _already_ran_today(rc, date_key, origin=o) for o, _ in _origins_to_run()
        ):
            return {"ran": False, "reason": "already_ran"}

    per_origin: dict[str, dict] = {}
    for origin_label, base_url in _origins_to_run():
        per_origin[origin_label] = _run_single_origin(
            db, rc, origin_label, base_url, now, date_key, force=force,
        )

    local = per_origin.get("local") or {}
    total_regressions = sum(
        (p.get("regressions") or 0) for p in per_origin.values() if p.get("ran")
    )
    total_alerts = sum(
        (p.get("alerts_fired") or 0) for p in per_origin.values() if p.get("ran")
    )

    top: dict = {
        "ran": bool(local.get("ran")),
        "origins": per_origin,
        "regressions": total_regressions,
        "alerts_fired": total_alerts,
    }
    # Mirror the legacy top-level fields from the local origin so existing
    # ops endpoints / tests keep seeing `status`, `elapsed_seconds`, etc.
    for field in ("status", "elapsed_seconds", "routes", "reason", "per_route"):
        if field in local:
            top[field] = local[field]
    return top
