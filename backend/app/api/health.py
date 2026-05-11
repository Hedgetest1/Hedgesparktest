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

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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


import logging
log = logging.getLogger(__name__)


@router.get("/system/health")
def system_health():
    """
    Structured system health check.  No authentication required — this is
    an operational monitoring endpoint (same pattern as /health).

    Security note (born 2026-05-11 Sprint A audit C3): exception
    detail strings are NEVER returned to anonymous callers (used to
    leak SQLAlchemy/psycopg connection-string fragments, schema/table
    names, host/port; redis-py errors leaked socket addresses).
    Verbose detail goes to server logs (log.warning) only — operators
    can correlate via `request_id` if needed. Public response carries
    coarse status codes (db_unreachable, redis_error, etc.) suitable
    for monitoring without aiding recon.
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
        # Verbose detail to logs only — never to anonymous response.
        log.warning("system_health: database probe failed: %s", exc)
        report["subsystems"]["database"] = {
            "status": "error",
            "code": "db_unreachable",
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
        log.warning("system_health: redis probe failed: %s", exc)
        report["subsystems"]["redis"] = {
            "status": "error",
            "code": "redis_error",
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
            log.warning("system_health: worker probe failed: %s", exc)
            workers_report = {"status": "error", "code": "worker_probe_failed"}

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
            log.warning("system_health: event_ingestion probe failed: %s", exc)
            report["subsystems"]["event_ingestion"] = {
                "status": "error",
                "code": "event_probe_failed",
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

    # ── 6. Version — immutable snapshot of the running process ───────────
    # Captured ONCE at module import so it reflects the code actually
    # loaded by the running python process, not the disk at request time.
    # This is how deploy_gate.py verifies that a pm2 restart actually
    # loaded the new code (see scripts/deploy_gate.py postdeploy).
    try:
        from app.core.version import get_version_info
        report["version"] = get_version_info()
    except Exception:
        report["version"] = {"git_sha": "unknown"}

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


class MerchantSipStatusResponse(BaseModel):
    """GET /merchant/sip-status — dashboard system status bar payload."""
    data_points: int
    confidence: str
    trust_score: float
    autonomy_level: int
    signals_active: int
    nudges_active: int


@router.get(
    "/merchant/sip-status",
    response_model=MerchantSipStatusResponse,
    response_model_exclude_none=False,
)
def merchant_sip_status(shop: str = ""):
    """
    Lightweight SIP status for the dashboard system status bar.
    Returns data_points, confidence, active signals/nudges count.
    Authenticated via session cookie (shop param for routing).
    """
    if not shop:
        raise HTTPException(status_code=400, detail="missing shop")
    try:
        with engine.connect() as conn:
            # SIP data
            sip = conn.execute(
                text("""
                    SELECT data_points_total, confidence_level, trust_score, autonomy_level
                    FROM store_intelligence_profiles WHERE shop_domain = :shop
                """),
                {"shop": shop},
            ).fetchone()

            # Active signals
            signals = conn.execute(
                text("SELECT COUNT(*) FROM opportunity_signals WHERE shop_domain = :shop AND expires_at > NOW()"),
                {"shop": shop},
            ).scalar() or 0

            # Active nudges
            nudges = conn.execute(
                text("SELECT COUNT(*) FROM active_nudges WHERE shop_domain = :shop AND status = 'active'"),
                {"shop": shop},
            ).scalar() or 0

        return {
            "data_points": sip[0] if sip else 0,
            "confidence": sip[1] if sip else "none",
            "trust_score": round(float(sip[2]), 2) if sip and sip[2] else 0.5,
            "autonomy_level": sip[3] if sip else 0,
            "signals_active": signals,
            "nudges_active": nudges,
        }
    except Exception as exc:
        log.warning("merchant_intelligence_snapshot: DB read failed: %s", exc)
        return {
            "data_points": 0, "confidence": "none", "trust_score": 0.5,
            "autonomy_level": 0, "signals_active": 0, "nudges_active": 0,
        }


@router.get("/ops/signal-count-week")
def signal_count_week():
    """
    Public endpoint: aggregate signal count over the last 7 days.
    Used by the landing page for social proof. No auth required.
    Lightweight — single COUNT query with index on detected_at.
    """
    try:
        import time as _time
        now_ms = int(_time.time() * 1000)
        week_ago = now_ms - 7 * 86_400 * 1_000
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM events WHERE timestamp > :ts"),
                {"ts": week_ago},
            ).fetchone()
        count = row[0] if row else 0
        return JSONResponse(
            content={"count": count},
            headers={"Cache-Control": "public, max-age=300"},  # 5-min cache
        )
    except Exception:
        return JSONResponse(content={"count": 0}, status_code=200)
