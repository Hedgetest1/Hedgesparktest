"""
public_status.py — Phase Ω' public status page backend.

  GET /public/status — anonymized, cache-friendly snapshot of system
                       health for the public status page. No PII, no
                       merchant data, no shop counts.

The shape is intentionally tight: status dots + uptime % + last incident.
Cached 60s in Redis to absorb traffic from the public page.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import engine

router = APIRouter(tags=["public_status"])
log = logging.getLogger(__name__)

_CACHE_KEY = "hs:public_status:v1"
_CACHE_TTL = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/public/status")
def get_public_status():
    """Public, unauthenticated, cache-friendly status snapshot."""
    # Try cache
    try:
        from app.core.redis_client import _client
        import json as _j
        rc = _client()
        if rc is not None:
            cached = rc.get(_CACHE_KEY)
            if cached:
                return _j.loads(cached)
    except Exception as exc:
        log.warning("public_status: cache read failed: %s", exc)
        rc = None

    components: list[dict] = []
    incidents: list[dict] = []

    # API
    try:
        t0 = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        components.append({
            "name": "API",
            "status": "operational" if latency_ms < 250 else "degraded",
            "latency_ms": latency_ms,
        })
    except Exception as exc:
        log.warning("public_status: api health check failed: %s", exc)
        components.append({"name": "API", "status": "outage", "latency_ms": None})

    # Database
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components.append({"name": "Database", "status": "operational"})
    except Exception as exc:
        log.warning("public_status: database health check failed: %s", exc)
        components.append({"name": "Database", "status": "outage"})

    # Workers — read worker_state freshness
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT worker_name, last_run_at FROM worker_state
                ORDER BY worker_name
            """)).fetchall()
        worker_status = "operational"
        worker_count = len(rows)
        stale = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for r in rows:
            last = r[1]
            if last is None:
                stale += 1
                continue
            age = (now - last).total_seconds()
            if age > 3600:  # 1h staleness floor
                stale += 1
        if stale > worker_count / 2:
            worker_status = "outage"
        elif stale > 0:
            worker_status = "degraded"
        components.append({
            "name": "Background workers",
            "status": worker_status,
            "stale_count": stale,
            "total_count": worker_count,
        })
    except Exception as exc:
        log.warning("public_status: worker state query failed: %s", exc)
        components.append({"name": "Background workers", "status": "unknown"})

    # Self-healing pipeline
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) FROM ops_alerts
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                  AND severity = 'critical'
            """)).first()
        critical_24h = int(row[0] or 0) if row else 0
        components.append({
            "name": "Self-healing pipeline",
            "status": "operational" if critical_24h == 0 else "degraded",
            "critical_24h": critical_24h,
        })
    except Exception as exc:
        log.warning("public_status: pipeline health query failed: %s", exc)
        components.append({"name": "Self-healing pipeline", "status": "unknown"})

    # Self-heal proof counter (MA-3) — the "receipts" competitors cannot
    # publish. Counts autonomous-pipeline actions (bugfix applied, governed
    # TIER_1 auto-apply, auto-approved) in 7d and 30d windows from the
    # append-only audit_log. No merchant data, no PII — pure action counts.
    # Rendered on the public /status page as "Pipeline fixed N incidents in
    # last 7 days" next to a link to the audit chain.
    self_heal_proof: dict = {
        "autonomous_fixes_7d": 0,
        "autonomous_fixes_30d": 0,
        "last_fix_at": None,
    }
    try:
        with engine.connect() as conn:
            # last_at carries the SAME 30d FILTER as n30 — an unfiltered
            # MAX(created_at) reports a >30d-old all-time fix as
            # "last_fix_at" while the counts are 0, which the public
            # status page renders as recent self-healing that never
            # happened (§0 no-false-claims). Invariant: 30d count 0 ⟺
            # last_fix_at NULL.
            row = conn.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS n7,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS n30,
                    MAX(created_at) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS last_at
                FROM audit_log
                WHERE action_type IN (
                    'bugfix_applied',
                    'bugfix_auto_approved',
                    'governed_tier1_applied'
                )
                  AND status = 'completed'
            """)).first()
        if row:
            self_heal_proof["autonomous_fixes_7d"] = int(row[0] or 0)
            self_heal_proof["autonomous_fixes_30d"] = int(row[1] or 0)
            last_at = row[2]
            self_heal_proof["last_fix_at"] = last_at.isoformat() if last_at else None
    except Exception as exc:
        log.warning("public_status: self_heal_proof query failed: %s", exc)

    # Recent incidents — last 7 days, critical only
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT created_at, source, summary
                FROM ops_alerts
                WHERE created_at >= NOW() - INTERVAL '7 days'
                  AND severity = 'critical'
                ORDER BY created_at DESC
                LIMIT 5
            """)).fetchall()
        for r in rows:
            incidents.append({
                "at": r[0].isoformat() if r[0] else None,
                "component": r[1],
                "summary": (r[2] or "")[:140],
            })
    except Exception as exc:
        log.warning("public_status: incidents query failed: %s", exc)

    # Overall status
    statuses = [c.get("status") for c in components]
    if "outage" in statuses:
        overall = "outage"
    elif "degraded" in statuses:
        overall = "degraded"
    elif "unknown" in statuses:
        overall = "degraded"
    else:
        overall = "operational"

    result = {
        "overall": overall,
        "components": components,
        "incidents": incidents,
        "self_heal_proof": self_heal_proof,
        "checked_at": _now_iso(),
    }

    if rc is not None:
        try:
            import json as _j
            rc.setex(_CACHE_KEY, _CACHE_TTL, _j.dumps(result, default=str))
        except Exception as exc:
            log.warning("public_status: cache write failed: %s", exc)

    return result
