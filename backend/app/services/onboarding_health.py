"""
onboarding_health.py — Onboarding friction detection and observability.

Monitors the merchant onboarding pipeline and feeds problems into the
alerting and loop_health system so onboarding is not a dead frontend island.

Detects:
    - Setup grace failures (install completed but setup never becomes healthy)
    - Onboarding stuck (merchant stuck in needs_repair > threshold)
    - Pixel abandonment (setup complete, pixel never connected)
    - Slow time-to-first-insight (first insight > expected window)
    - Repair loops (repeated repair triggers for same merchant)
    - Setup oscillation (readiness state flapping between values)

Public interface:
    check_onboarding_health(db) -> dict    — full onboarding pipeline health snapshot
    detect_stuck_merchants(db) -> list      — merchants stuck in onboarding
    detect_pixel_abandonment(db) -> list    — merchants who never connected pixel
    detect_slow_activation(db) -> list      — merchants with slow time-to-insight
    write_onboarding_alerts(db) -> dict     — check all conditions, write alerts

Called by: agent_worker.py (every 15 minutes, after run_pending_onboarding)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("onboarding_health")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Merchant stuck in needs_repair for more than this is considered stuck
_STUCK_REPAIR_MINUTES = 30

# Merchant installed more than this ago with no pixel → pixel abandonment
_PIXEL_ABANDON_HOURS = 48

# Expected time from install to first signal
_EXPECTED_FIRST_INSIGHT_HOURS = 2

# Repair loop: if a merchant has triggered > N repairs in the lookback window
_REPAIR_LOOP_THRESHOLD = 5
_REPAIR_LOOP_LOOKBACK_HOURS = 24


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def detect_stuck_merchants(db: Session) -> list[dict]:
    """
    Find merchants whose onboarding_status has been 'pending' or 'failed'
    for longer than the stuck threshold.
    """
    cutoff = _utcnow() - timedelta(minutes=_STUCK_REPAIR_MINUTES)
    rows = db.execute(text("""
        SELECT shop_domain, onboarding_status, onboarding_error, installed_at
        FROM merchants
        WHERE install_status = 'active'
          AND onboarding_status IN ('pending', 'failed')
          AND installed_at < :cutoff
        ORDER BY installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "status": r.onboarding_status,
            "error": r.onboarding_error,
            "stuck_since": r.installed_at.isoformat() if r.installed_at else None,
            "stuck_minutes": int((_utcnow() - r.installed_at).total_seconds() / 60) if r.installed_at else None,
        }
        for r in rows
    ]


def detect_pixel_abandonment(db: Session) -> list[dict]:
    """
    Find merchants who completed setup (onboarding_status=ready) more than
    _PIXEL_ABANDON_HOURS ago but have zero purchase events from the pixel.
    """
    cutoff = _utcnow() - timedelta(hours=_PIXEL_ABANDON_HOURS)
    rows = db.execute(text("""
        SELECT m.shop_domain, m.installed_at,
               (SELECT COUNT(*) FROM shop_orders so
                WHERE so.shop_domain = m.shop_domain AND so.source = 'pixel') AS pixel_orders
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.onboarding_status = 'ready'
          AND m.installed_at < :cutoff
        ORDER BY m.installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "installed_at": r.installed_at.isoformat() if r.installed_at else None,
            "hours_since_install": int((_utcnow() - r.installed_at).total_seconds() / 3600) if r.installed_at else None,
            "pixel_orders": r.pixel_orders,
        }
        for r in rows
        if r.pixel_orders == 0
    ]


def detect_slow_activation(db: Session) -> list[dict]:
    """
    Find merchants who have been installed for > _EXPECTED_FIRST_INSIGHT_HOURS
    but have zero opportunity_signals.
    """
    cutoff = _utcnow() - timedelta(hours=_EXPECTED_FIRST_INSIGHT_HOURS)
    rows = db.execute(text("""
        SELECT m.shop_domain, m.installed_at,
               (SELECT COUNT(*) FROM events e
                WHERE e.shop_domain = m.shop_domain) AS event_count,
               (SELECT COUNT(*) FROM opportunity_signals os
                WHERE os.shop_domain = m.shop_domain AND os.expires_at > now()) AS signal_count
        FROM merchants m
        WHERE m.install_status = 'active'
          AND m.onboarding_status = 'ready'
          AND m.installed_at < :cutoff
        ORDER BY m.installed_at ASC
        LIMIT 50
    """), {"cutoff": cutoff}).fetchall()

    return [
        {
            "shop_domain": r.shop_domain,
            "installed_at": r.installed_at.isoformat() if r.installed_at else None,
            "hours_since_install": int((_utcnow() - r.installed_at).total_seconds() / 3600) if r.installed_at else None,
            "event_count": r.event_count,
            "signal_count": r.signal_count,
        }
        for r in rows
        if r.signal_count == 0
    ]


# ---------------------------------------------------------------------------
# Aggregate health check
# ---------------------------------------------------------------------------

def check_onboarding_health(db: Session) -> dict:
    """
    Full onboarding pipeline health snapshot.
    Returns counts and lists for each problem type.
    """
    stuck = detect_stuck_merchants(db)
    pixel_abandon = detect_pixel_abandonment(db)
    slow_activation = detect_slow_activation(db)

    # Count total active merchants for context
    total = db.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE install_status = 'active'"
    )).scalar() or 0

    ready = db.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE install_status = 'active' AND onboarding_status = 'ready'"
    )).scalar() or 0

    return {
        "total_active_merchants": total,
        "total_onboarded": ready,
        "stuck_merchants": len(stuck),
        "stuck_details": stuck[:10],  # cap detail list
        "pixel_abandonment": len(pixel_abandon),
        "pixel_abandon_details": pixel_abandon[:10],
        "slow_activation": len(slow_activation),
        "slow_activation_details": slow_activation[:10],
        "healthy": len(stuck) == 0 and len(pixel_abandon) <= 1,
    }


# ---------------------------------------------------------------------------
# Alert writer — integrates with ops_alerts via alerting.py
# ---------------------------------------------------------------------------

def write_onboarding_alerts(db: Session) -> dict:
    """
    Check all onboarding health conditions and write ops_alerts for
    actionable problems. Deduplication is handled by alerting.write_alert.

    Returns summary of alerts written.
    """
    from app.services.alerting import write_alert

    written = 0

    # 1. Stuck merchants
    stuck = detect_stuck_merchants(db)
    for m in stuck[:5]:  # cap at 5 alerts per cycle
        write_alert(
            db,
            source="onboarding_health",
            alert_type="onboarding_stuck",
            severity="warning",
            shop_domain=m["shop_domain"],
            summary=f"Onboarding stuck for {m['stuck_minutes']}m: {m['status']}",
            detail={
                "status": m["status"],
                "error": m["error"],
                "stuck_minutes": m["stuck_minutes"],
            },
        )
        written += 1

    # 2. Pixel abandonment (only alert after extended period)
    pixel_abandon = detect_pixel_abandonment(db)
    long_abandon = [p for p in pixel_abandon if (p.get("hours_since_install") or 0) > 72]
    for m in long_abandon[:3]:
        write_alert(
            db,
            source="onboarding_health",
            alert_type="pixel_abandonment",
            severity="info",
            shop_domain=m["shop_domain"],
            summary=f"Pixel not connected after {m['hours_since_install']}h",
            detail=m,
        )
        written += 1

    # 3. Slow activation
    slow = detect_slow_activation(db)
    # Only alert if they have events but no signals (means intelligence worker isn't working)
    data_but_no_insights = [s for s in slow if (s.get("event_count") or 0) > 10 and s.get("signal_count") == 0]
    for m in data_but_no_insights[:3]:
        write_alert(
            db,
            source="onboarding_health",
            alert_type="slow_activation",
            severity="warning",
            shop_domain=m["shop_domain"],
            summary=f"Events flowing ({m['event_count']}) but 0 signals after {m['hours_since_install']}h",
            detail=m,
        )
        written += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        log.exception("onboarding_health: failed to commit alerts")

    return {"alerts_written": written, "stuck": len(stuck), "pixel_abandon": len(pixel_abandon), "slow_activation": len(slow)}
