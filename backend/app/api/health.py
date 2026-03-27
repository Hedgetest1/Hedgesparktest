"""
System health endpoint — structured operational health for operators,
monitoring, and future AI orchestration.

GET /system/health

Returns a structured JSON report with per-subsystem status:
  - database: connectivity + latency
  - redis: connectivity + mode
  - workers: freshness per worker (from worker_state table)
  - event_ingestion: recent activity volume
  - ai_cache: availability

Overall status:
  - "ok"       — all subsystems healthy
  - "degraded" — non-critical subsystem stale or unavailable
  - "critical" — database unreachable (nothing works)
"""
from __future__ import annotations

import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database import engine

router = APIRouter(tags=["system"])

log = logging.getLogger(__name__)

# Worker cycle intervals (seconds) — used to determine staleness.
# A worker is "stale" if its last_run_at exceeds 2× its expected cycle interval.
_WORKER_INTERVALS = {
    "aggregation_worker":          300,   # 5 min
    "intelligence_worker":         600,   # 10 min
    "segment_monitor_worker":      300,   # 5 min
    "agent_worker":                900,   # 15 min
    "nudge_optimization_worker":   21600, # 6 hours
    "gdpr_worker":                 300,   # 5 min
}

_STALENESS_MULTIPLIER = 2.5  # stale if last_run_at > interval × this


@router.get("/system/health")
def system_health():
    """
    Structured system health check.  No authentication required — this is
    an operational monitoring endpoint (same pattern as /health).
    """
    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "subsystems": {},
    }
    issues: list[str] = []
    critical = False

    # ── 1. Database ───────────────────────────────────────────────────────
    try:
        t0 = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        report["subsystems"]["database"] = {
            "status": "ok",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        report["subsystems"]["database"] = {
            "status": "error",
            "detail": str(exc)[:200],
        }
        issues.append("database unreachable")
        critical = True

    # ── 2. Redis ──────────────────────────────────────────────────────────
    try:
        from app.core.redis_client import _client
        client = _client()
        if client is None:
            report["subsystems"]["redis"] = {"status": "unavailable", "mode": "noop"}
            issues.append("redis unavailable")
        else:
            t0 = time.monotonic()
            client.ping()
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            report["subsystems"]["redis"] = {
                "status": "ok",
                "mode": "connected",
                "latency_ms": latency_ms,
            }
    except Exception as exc:
        report["subsystems"]["redis"] = {
            "status": "error",
            "detail": str(exc)[:200],
        }
        issues.append("redis error")

    # ── 3. Worker freshness ───────────────────────────────────────────────
    workers_report: dict = {}
    if not critical:
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT worker_name, last_run_at FROM worker_state"
                )).fetchall()

            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            worker_map = {r[0]: r[1] for r in rows}

            for wname, expected_interval in _WORKER_INTERVALS.items():
                last_run = worker_map.get(wname)
                if last_run is None:
                    workers_report[wname] = {"status": "never_run", "last_run": None}
                    # Not necessarily an issue — worker may not have started yet
                else:
                    age_s = (now_utc - last_run).total_seconds()
                    stale_threshold = expected_interval * _STALENESS_MULTIPLIER
                    status = "ok" if age_s <= stale_threshold else "stale"
                    workers_report[wname] = {
                        "status": status,
                        "last_run": last_run.isoformat() + "Z",
                        "age_seconds": round(age_s),
                        "threshold_seconds": round(stale_threshold),
                    }
                    if status == "stale":
                        issues.append(f"worker {wname} stale ({round(age_s)}s)")
        except Exception as exc:
            workers_report = {"error": str(exc)[:200]}

    report["subsystems"]["workers"] = workers_report

    # ── 4. Event ingestion ────────────────────────────────────────────────
    if not critical:
        try:
            now_epoch_ms = int(time.time() * 1000)
            one_hour_ago = now_epoch_ms - 3_600_000
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT COUNT(*) FROM events WHERE timestamp > :ts"
                ), {"ts": one_hour_ago}).fetchone()
            count = row[0] if row else 0
            report["subsystems"]["event_ingestion"] = {
                "status": "ok" if count > 0 else "quiet",
                "events_last_hour": count,
            }
            if count == 0:
                issues.append("no events in last hour")
        except Exception as exc:
            report["subsystems"]["event_ingestion"] = {
                "status": "error",
                "detail": str(exc)[:200],
            }

    # ── 5. AI cache ───────────────────────────────────────────────────────
    try:
        from app.core.redis_client import _client as _rc
        client = _rc()
        if client is not None:
            # Count AI compose cache keys (lightweight pattern scan)
            count = 0
            for _ in client.scan_iter(match="hs:ai_compose:*", count=100):
                count += 1
                if count >= 1000:
                    break
            report["subsystems"]["ai_cache"] = {
                "status": "ok",
                "cached_entries": count,
            }
        else:
            report["subsystems"]["ai_cache"] = {"status": "unavailable"}
    except Exception:
        report["subsystems"]["ai_cache"] = {"status": "unavailable"}

    # ── Overall status ────────────────────────────────────────────────────
    if critical:
        report["status"] = "critical"
    elif issues:
        report["status"] = "degraded"
    else:
        report["status"] = "ok"

    report["issues"] = issues

    status_code = 503 if critical else 200
    return JSONResponse(content=report, status_code=status_code)
