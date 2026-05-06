"""
onboarding_funnel.py — Onboarding funnel computation, friction detection,
and improvement signal generation.

Public interface:
    record_event(db, shop, event_type, **kw) -> OnboardingEvent
    get_shop_funnel(db, shop) -> dict          — per-shop funnel state
    get_aggregate_funnel(db, days) -> dict      — global funnel metrics
    detect_friction(db) -> list[dict]           — friction signals across all shops
    generate_insights(db) -> list[dict]         — actionable improvement suggestions
    run_friction_detection(db) -> dict          — full cycle: detect + alert + summarize

Called by:
    - POST /onboarding/event (frontend → record_event)
    - agent_worker (every 15 min → run_friction_detection)
    - GET /ops/onboarding-funnel (operator view)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app.models.onboarding_event import OnboardingEvent

log = logging.getLogger("onboarding_funnel")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Ordered funnel milestones — defines the canonical funnel
FUNNEL_MILESTONES = [
    "install_completed",
    "setup_completed",
    "pixel_viewed",
    "pixel_copy_clicked",
    "pixel_confirmed",
    "pixel_detected",
    "first_visitor_detected",
    "first_insight_generated",
    "onboarding_complete",
]

# Events that are NOT milestones (allow duplicates)
INTERACTION_EVENTS = {
    "pixel_skipped",
    "welcome_dismissed",
    "onboarding_dismissed",
    "repair_triggered",
    "setup_retry",
    "upgrade_clicked",
    "session_start",
}

ALL_EVENT_TYPES = set(FUNNEL_MILESTONES) | INTERACTION_EVENTS

# Friction detection thresholds
_PIXEL_VIEW_NO_ACTION_MINUTES = 30    # viewed pixel instructions, no copy/confirm
_PIXEL_COPY_NO_CONFIRM_MINUTES = 60   # copied code but never confirmed
_MULTIPLE_SESSIONS_THRESHOLD = 3       # N sessions without completing onboarding
_SLOW_FIRST_INSIGHT_HOURS = 4          # setup done, no insight after N hours
_ONBOARDING_SHOWN_THRESHOLD = 5        # onboarding shown N+ times without complete

# Friction lookback — only scan events from the last N days (prevents
# generating alerts for merchants who stalled months ago)
_FRICTION_LOOKBACK_DAYS = 14

# Alert cooldown — suppress repeated friction alerts for the same
# (shop_domain, signal) within this window.  The alerting.py dedup is
# only 5 min, far too short for a 15-min detection cycle.
_FRICTION_ALERT_COOLDOWN_SECONDS = 86400  # 24 hours


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------

def record_event(
    db: Session,
    shop_domain: str,
    event_type: str,
    *,
    session_number: int | None = None,
    context: dict[str, Any] | None = None,
) -> OnboardingEvent | None:
    """
    Record a single onboarding event.

    For milestone events: idempotent per (shop_domain, event_type).
    For interaction events: always creates a new row.

    Returns the event row, or None if a milestone was already recorded.
    """
    if event_type not in ALL_EVENT_TYPES:
        log.warning("onboarding_funnel: unknown event_type=%s shop=%s", event_type, shop_domain)
        return None

    is_milestone = event_type in FUNNEL_MILESTONES

    # Reinstall handling — if this is a new install_completed but one already
    # exists, the merchant reinstalled.  Clear old milestone events so the
    # funnel starts fresh.  Interaction events are preserved for history.
    if event_type == "install_completed" and is_milestone:
        existing_install = db.query(OnboardingEvent).filter(
            OnboardingEvent.shop_domain == shop_domain,
            OnboardingEvent.event_type == "install_completed",
        ).first()
        if existing_install:
            deleted = (
                db.query(OnboardingEvent)
                .filter(
                    OnboardingEvent.shop_domain == shop_domain,
                    OnboardingEvent.event_type.in_(FUNNEL_MILESTONES),
                )
                .delete(synchronize_session="fetch")
            )
            log.info(
                "onboarding_funnel: reinstall detected for shop=%s — cleared %d old milestones",
                shop_domain, deleted,
            )
    elif is_milestone:
        # Milestone dedup — skip if already recorded
        existing = db.query(OnboardingEvent).filter(
            OnboardingEvent.shop_domain == shop_domain,
            OnboardingEvent.event_type == event_type,
        ).first()
        if existing:
            return None  # already recorded

    # Compute elapsed_seconds from previous milestone
    elapsed: float | None = None
    if is_milestone:
        prev = db.query(OnboardingEvent).filter(
            OnboardingEvent.shop_domain == shop_domain,
            OnboardingEvent.event_type.in_(FUNNEL_MILESTONES),
        ).order_by(OnboardingEvent.created_at.desc()).first()
        if prev:
            elapsed = (_utcnow() - prev.created_at).total_seconds()

    ctx_str: str | None = None
    if context:
        ctx_str = json.dumps(context, default=str)[:512]

    event = OnboardingEvent(
        shop_domain=shop_domain,
        event_type=event_type,
        elapsed_seconds=elapsed,
        session_number=session_number,
        context=ctx_str,
    )
    db.add(event)
    db.flush()

    log.info(
        "onboarding_event: shop=%s type=%s elapsed=%s session=%s",
        shop_domain, event_type,
        f"{elapsed:.0f}s" if elapsed else "N/A",
        session_number,
    )
    return event


# ---------------------------------------------------------------------------
# Per-shop funnel state
# ---------------------------------------------------------------------------

def get_shop_funnel(db: Session, shop_domain: str) -> dict:
    """
    Build the funnel state for a single shop.

    Returns:
        {
            shop_domain: str,
            milestones: {event_type: {reached: bool, at: iso_str, elapsed_seconds: float}},
            current_step: str,           # latest milestone reached
            next_step: str | None,       # next milestone to reach
            completion_pct: float,       # 0.0 - 1.0
            total_elapsed_seconds: float,# from first to latest milestone
            interaction_counts: {event_type: int},
            session_count: int,
        }
    """
    # Fetch all milestone events for this shop
    milestones = db.query(OnboardingEvent).filter(
        OnboardingEvent.shop_domain == shop_domain,
        OnboardingEvent.event_type.in_(FUNNEL_MILESTONES),
    ).order_by(OnboardingEvent.created_at.asc()).all()

    milestone_map: dict[str, dict] = {}
    for m in milestones:
        milestone_map[m.event_type] = {
            "reached": True,
            "at": m.created_at.isoformat() if m.created_at else None,
            "elapsed_seconds": m.elapsed_seconds,
        }

    # Find current and next step
    current_step = "not_started"
    next_step: str | None = FUNNEL_MILESTONES[0]
    for i, step in enumerate(FUNNEL_MILESTONES):
        if step in milestone_map:
            current_step = step
            next_step = FUNNEL_MILESTONES[i + 1] if i + 1 < len(FUNNEL_MILESTONES) else None
        else:
            # Fill in unreached milestones
            if step not in milestone_map:
                milestone_map[step] = {"reached": False, "at": None, "elapsed_seconds": None}

    # Completion percentage
    reached_count = sum(1 for s in FUNNEL_MILESTONES if milestone_map.get(s, {}).get("reached"))
    completion_pct = reached_count / len(FUNNEL_MILESTONES)

    # Total elapsed
    first_at = milestones[0].created_at if milestones else None
    last_at = milestones[-1].created_at if milestones else None
    total_elapsed = (last_at - first_at).total_seconds() if first_at and last_at else 0.0

    # Interaction counts — use ORM to avoid tuple-binding issues with text()
    interaction_rows = (
        db.query(OnboardingEvent.event_type, func.count(OnboardingEvent.id))
        .filter(
            OnboardingEvent.shop_domain == shop_domain,
            OnboardingEvent.event_type.in_(list(INTERACTION_EVENTS)),
        )
        .group_by(OnboardingEvent.event_type)
        .all()
    )

    interaction_counts = {r[0]: r[1] for r in interaction_rows}

    # Session count
    max_session = db.query(func.max(OnboardingEvent.session_number)).filter(
        OnboardingEvent.shop_domain == shop_domain,
    ).scalar() or 0

    return {
        "shop_domain": shop_domain,
        "milestones": milestone_map,
        "current_step": current_step,
        "next_step": next_step,
        "completion_pct": round(completion_pct, 3),
        "total_elapsed_seconds": round(total_elapsed, 1),
        "interaction_counts": interaction_counts,
        "session_count": max_session,
    }


# ---------------------------------------------------------------------------
# Aggregate funnel metrics
# ---------------------------------------------------------------------------

def get_aggregate_funnel(db: Session, days: int = 30) -> dict:
    """
    Compute aggregate funnel metrics across all shops within the lookback window.

    Returns:
        {
            period_days: int,
            total_installs: int,
            funnel: [{step, reached, pct, median_elapsed_seconds, drop_off_pct}],
            conversion_rates: {step_a -> step_b: pct},
            median_time_to_complete: float,
            avg_sessions_to_complete: float,
        }
    """
    # Operator/dev tenant exclusion (founder direttiva 2026-05-06).
    from app.core.operator_blocklist import operator_dev_shops
    cutoff = _utcnow() - timedelta(days=days)

    # Count shops that started onboarding in this period
    total_installs = db.execute(text("""
        SELECT COUNT(DISTINCT shop_domain)
        FROM onboarding_events
        WHERE event_type = 'install_completed' AND created_at >= :cutoff
          AND NOT (shop_domain = ANY(:operator_shops))
    """), {"cutoff": cutoff, "operator_shops": list(operator_dev_shops())}).scalar() or 0

    if total_installs == 0:
        return {
            "period_days": days,
            "total_installs": 0,
            "funnel": [],
            "conversion_rates": {},
            "median_time_to_complete": None,
            "avg_sessions_to_complete": None,
        }

    # Per-step: count distinct shops that reached each milestone +
    # median elapsed seconds. Single GROUP BY query over all milestones
    # (was 2 queries × len(FUNNEL_MILESTONES) = 18 round-trips → 1).
    # Only count milestones recorded AFTER the cutoff (prevents stale
    # milestones from inflating conversion rates for shops that installed
    # within the window but had milestone events from a prior period).
    # Operator/dev tenant exclusion (founder direttiva 2026-05-06).
    from app.core.operator_blocklist import operator_dev_shops
    _operator_shops = list(operator_dev_shops())
    rows = db.execute(text("""
        SELECT
            event_type,
            COUNT(DISTINCT shop_domain) AS reached,
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY elapsed_seconds
            ) FILTER (WHERE elapsed_seconds IS NOT NULL) AS median_elapsed
        FROM onboarding_events
        WHERE event_type = ANY(:steps)
          AND created_at >= :cutoff
          AND shop_domain IN (
              SELECT shop_domain FROM merchants
              WHERE install_status = 'active' AND installed_at >= :cutoff
                AND NOT (shop_domain = ANY(:operator_shops))
          )
        GROUP BY event_type
    """), {"steps": list(FUNNEL_MILESTONES), "cutoff": cutoff, "operator_shops": _operator_shops}).fetchall()

    by_step = {row[0]: (int(row[1] or 0), row[2]) for row in rows}

    funnel_steps = []
    prev_count = total_installs
    # Iterate in canonical FUNNEL_MILESTONES order (SQL GROUP BY result
    # order is undefined; we must preserve the funnel sequence for
    # conversion-rate math below).
    for step in FUNNEL_MILESTONES:
        reached, median_elapsed = by_step.get(step, (0, None))

        pct = round(reached / total_installs, 3) if total_installs > 0 else 0
        drop_off = round(1.0 - (reached / prev_count), 3) if prev_count > 0 else 0

        funnel_steps.append({
            "step": step,
            "reached": reached,
            "pct": pct,
            "median_elapsed_seconds": round(median_elapsed, 1) if median_elapsed else None,
            "drop_off_pct": drop_off,
        })
        prev_count = reached

    # Step-to-step conversion rates
    conversion_rates = {}
    for i in range(len(funnel_steps) - 1):
        a = funnel_steps[i]
        b = funnel_steps[i + 1]
        if a["reached"] > 0:
            rate = round(b["reached"] / a["reached"], 3)
            conversion_rates[f"{a['step']} -> {b['step']}"] = rate

    # Median total time from install_completed to onboarding_complete
    median_total = db.execute(text("""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_time)
        FROM (
            SELECT
                MAX(CASE WHEN event_type = 'onboarding_complete' THEN created_at END)
                - MAX(CASE WHEN event_type = 'install_completed' THEN created_at END)
                AS total_time
            FROM onboarding_events
            WHERE shop_domain IN (
                SELECT shop_domain FROM merchants
                WHERE install_status = 'active' AND installed_at >= :cutoff
                  AND NOT (shop_domain = ANY(:operator_shops))
            )
            GROUP BY shop_domain
            HAVING MAX(CASE WHEN event_type = 'onboarding_complete' THEN 1 END) = 1
        ) sub
    """), {"cutoff": cutoff, "operator_shops": _operator_shops}).scalar()

    median_total_seconds = None
    if median_total is not None:
        try:
            median_total_seconds = round(median_total.total_seconds(), 1)
        except AttributeError:
            pass

    # Average sessions to complete
    avg_sessions = db.execute(text("""
        SELECT AVG(max_session)
        FROM (
            SELECT MAX(session_number) as max_session
            FROM onboarding_events
            WHERE event_type = 'onboarding_complete'
              AND shop_domain IN (
                  SELECT shop_domain FROM merchants
                  WHERE install_status = 'active' AND installed_at >= :cutoff
                    AND NOT (shop_domain = ANY(:operator_shops))
              )
            GROUP BY shop_domain
        ) sub
    """), {"cutoff": cutoff, "operator_shops": _operator_shops}).scalar()

    return {
        "period_days": days,
        "total_installs": total_installs,
        "funnel": funnel_steps,
        "conversion_rates": conversion_rates,
        "median_time_to_complete": median_total_seconds,
        "avg_sessions_to_complete": round(float(avg_sessions), 1) if avg_sessions else None,
    }


# ---------------------------------------------------------------------------
# Friction detection
# ---------------------------------------------------------------------------

def detect_friction(db: Session) -> list[dict]:
    """
    Scan for merchants exhibiting friction patterns.

    Only considers events within _FRICTION_LOOKBACK_DAYS to avoid
    generating alerts for merchants who stalled months ago.

    Returns list of:
        {shop_domain, signal, severity, detail}
    """
    now = _utcnow()
    lookback_floor = now - timedelta(days=_FRICTION_LOOKBACK_DAYS)
    signals: list[dict] = []

    # 1. Pixel viewed but no action (copy/confirm/skip) after threshold
    pixel_viewed_cutoff = now - timedelta(minutes=_PIXEL_VIEW_NO_ACTION_MINUTES)
    stalled_at_pixel = db.execute(text("""
        SELECT oe.shop_domain, oe.created_at
        FROM onboarding_events oe
        WHERE oe.event_type = 'pixel_viewed'
          AND oe.created_at < :cutoff
          AND oe.created_at >= :floor
          AND NOT EXISTS (
              SELECT 1 FROM onboarding_events oe2
              WHERE oe2.shop_domain = oe.shop_domain
                AND oe2.event_type IN ('pixel_copy_clicked', 'pixel_confirmed', 'pixel_skipped', 'pixel_detected')
          )
        LIMIT 50
    """), {"cutoff": pixel_viewed_cutoff, "floor": lookback_floor}).fetchall()

    for r in stalled_at_pixel:
        elapsed = (now - r.created_at).total_seconds() / 60
        signals.append({
            "shop_domain": r.shop_domain,
            "signal": "onboarding_dropoff_pixel",
            "severity": "warning" if elapsed > 120 else "info",
            "detail": f"Viewed pixel instructions {elapsed:.0f}m ago, no action taken",
        })

    # 2. Pixel code copied but never confirmed/detected
    pixel_copy_cutoff = now - timedelta(minutes=_PIXEL_COPY_NO_CONFIRM_MINUTES)
    copied_not_confirmed = db.execute(text("""
        SELECT oe.shop_domain, oe.created_at
        FROM onboarding_events oe
        WHERE oe.event_type = 'pixel_copy_clicked'
          AND oe.created_at < :cutoff
          AND oe.created_at >= :floor
          AND NOT EXISTS (
              SELECT 1 FROM onboarding_events oe2
              WHERE oe2.shop_domain = oe.shop_domain
                AND oe2.event_type IN ('pixel_confirmed', 'pixel_detected')
          )
        LIMIT 50
    """), {"cutoff": pixel_copy_cutoff, "floor": lookback_floor}).fetchall()

    for r in copied_not_confirmed:
        elapsed = (now - r.created_at).total_seconds() / 60
        signals.append({
            "shop_domain": r.shop_domain,
            "signal": "onboarding_pixel_copy_stall",
            "severity": "info",
            "detail": f"Copied pixel code {elapsed:.0f}m ago but never confirmed",
        })

    # 3. Multiple sessions without completing onboarding (bounded by lookback)
    multi_session = db.execute(text("""
        SELECT shop_domain, MAX(session_number) as sessions
        FROM onboarding_events
        WHERE session_number IS NOT NULL
          AND created_at >= :floor
        GROUP BY shop_domain
        HAVING MAX(session_number) >= :threshold
           AND shop_domain NOT IN (
               SELECT shop_domain FROM onboarding_events
               WHERE event_type = 'onboarding_complete'
           )
        LIMIT 50
    """), {"threshold": _MULTIPLE_SESSIONS_THRESHOLD, "floor": lookback_floor}).fetchall()

    for r in multi_session:
        signals.append({
            "shop_domain": r.shop_domain,
            "signal": "onboarding_confusion",
            "severity": "warning",
            "detail": f"{r.sessions} sessions without completing onboarding",
        })

    # 4. Setup completed but no first_insight after threshold
    slow_insight_cutoff = now - timedelta(hours=_SLOW_FIRST_INSIGHT_HOURS)
    slow_insight = db.execute(text("""
        SELECT oe.shop_domain, oe.created_at
        FROM onboarding_events oe
        WHERE oe.event_type = 'setup_completed'
          AND oe.created_at < :cutoff
          AND oe.created_at >= :floor
          AND NOT EXISTS (
              SELECT 1 FROM onboarding_events oe2
              WHERE oe2.shop_domain = oe.shop_domain
                AND oe2.event_type = 'first_insight_generated'
          )
        LIMIT 50
    """), {"cutoff": slow_insight_cutoff, "floor": lookback_floor}).fetchall()

    for r in slow_insight:
        elapsed_h = (now - r.created_at).total_seconds() / 3600
        signals.append({
            "shop_domain": r.shop_domain,
            "signal": "onboarding_slow_progress",
            "severity": "warning" if elapsed_h > 12 else "info",
            "detail": f"Setup completed {elapsed_h:.1f}h ago, no insights yet",
        })

    # 5. Pixel skipped — track but lower severity (bounded by lookback)
    skipped = db.execute(text("""
        SELECT shop_domain, COUNT(*) as skip_count
        FROM onboarding_events
        WHERE event_type = 'pixel_skipped'
          AND created_at >= :floor
        GROUP BY shop_domain
        HAVING shop_domain NOT IN (
            SELECT shop_domain FROM onboarding_events
            WHERE event_type IN ('pixel_confirmed', 'pixel_detected')
        )
        LIMIT 50
    """), {"floor": lookback_floor}).fetchall()

    for r in skipped:
        signals.append({
            "shop_domain": r.shop_domain,
            "signal": "onboarding_pixel_skipped",
            "severity": "info",
            "detail": f"Skipped pixel setup {r.skip_count} time(s)",
        })

    return signals


# ---------------------------------------------------------------------------
# Improvement insight generation
# ---------------------------------------------------------------------------

def generate_insights(db: Session, days: int = 30) -> list[dict]:
    """
    Generate actionable improvement suggestions based on funnel data.

    Returns list of:
        {insight, metric, severity, suggestion}
    """
    funnel = get_aggregate_funnel(db, days)
    insights: list[dict] = []

    if funnel["total_installs"] == 0:
        return insights

    steps = {s["step"]: s for s in funnel["funnel"]}
    total = funnel["total_installs"]

    # Insight 1: High pixel drop-off
    pixel_viewed = steps.get("pixel_viewed", {}).get("reached", 0)
    pixel_confirmed = steps.get("pixel_confirmed", {}).get("reached", 0)
    pixel_detected = steps.get("pixel_detected", {}).get("reached", 0)
    pixel_done = max(pixel_confirmed, pixel_detected)

    if pixel_viewed > 0 and pixel_done / pixel_viewed < 0.5:
        rate = round(pixel_done / pixel_viewed * 100, 1)
        insights.append({
            "insight": "pixel_conversion_low",
            "metric": f"{rate}% of merchants who view pixel instructions actually connect it",
            "severity": "high",
            "suggestion": "Simplify pixel instructions further, or explore auto-detection alternatives",
        })

    # Insight 2: High skip rate
    skip_count = db.execute(text("""
        SELECT COUNT(DISTINCT shop_domain) FROM onboarding_events
        WHERE event_type = 'pixel_skipped'
    """)).scalar() or 0
    if total > 5 and skip_count / total > 0.3:
        rate = round(skip_count / total * 100, 1)
        insights.append({
            "insight": "pixel_skip_rate_high",
            "metric": f"{rate}% of merchants skip pixel setup",
            "severity": "medium",
            "suggestion": "Consider making pixel value proposition clearer, or deferring pixel prompt",
        })

    # Insight 3: Slow time to first insight
    setup_to_insight = funnel["conversion_rates"].get("setup_completed -> first_insight_generated")
    if setup_to_insight and setup_to_insight < 0.4:
        insights.append({
            "insight": "slow_time_to_value",
            "metric": f"Only {round(setup_to_insight * 100, 1)}% reach first insight after setup",
            "severity": "high",
            "suggestion": "Check if intelligence worker is running, or lower signal detection thresholds",
        })

    # Insight 4: Multi-session onboarding
    avg_sessions = funnel.get("avg_sessions_to_complete")
    if avg_sessions and avg_sessions > 2.0:
        insights.append({
            "insight": "onboarding_takes_multiple_sessions",
            "metric": f"Average {avg_sessions} sessions to complete onboarding",
            "severity": "medium",
            "suggestion": "Onboarding should complete in one session — identify where merchants leave and return",
        })

    # Insight 5: Setup → pixel_viewed drop-off
    setup_done = steps.get("setup_completed", {}).get("reached", 0)
    if setup_done > 0 and pixel_viewed / setup_done < 0.7:
        rate = round(pixel_viewed / setup_done * 100, 1)
        insights.append({
            "insight": "pixel_visibility_low",
            "metric": f"Only {rate}% of merchants who complete setup see the pixel instructions",
            "severity": "medium",
            "suggestion": "Pixel instructions may not be prominent enough in the UI",
        })

    # Insight 6: Overall completion rate
    completed = steps.get("onboarding_complete", {}).get("reached", 0)
    if total > 5:
        rate = round(completed / total * 100, 1)
        insights.append({
            "insight": "overall_completion_rate",
            "metric": f"{rate}% overall onboarding completion ({completed}/{total})",
            "severity": "high" if rate < 30 else "medium" if rate < 60 else "low",
            "suggestion": "Target: >60% completion within 48 hours of install"
                if rate < 60 else "Completion rate is healthy",
        })

    return insights


# ---------------------------------------------------------------------------
# Full detection + alerting cycle (called by agent_worker)
# ---------------------------------------------------------------------------

def _friction_alert_suppressed(db: Session, alert_type: str, shop_domain: str | None) -> bool:
    """
    Check if a friction/insight alert was already written within the
    cooldown window.  Uses the ops_alerts table directly — this is a
    longer window than the generic 5-min dedup in alerting.py.
    """
    from app.models.ops_alert import OpsAlert

    cutoff = _utcnow() - timedelta(seconds=_FRICTION_ALERT_COOLDOWN_SECONDS)
    q = db.query(OpsAlert.id).filter(
        OpsAlert.source == "onboarding_funnel",
        OpsAlert.alert_type == alert_type,
        OpsAlert.created_at >= cutoff,
    )
    if shop_domain:
        q = q.filter(OpsAlert.shop_domain == shop_domain)
    else:
        q = q.filter(OpsAlert.shop_domain.is_(None))
    return q.first() is not None


def run_friction_detection(db: Session) -> dict:
    """
    Full friction detection cycle:
    1. Detect friction signals
    2. Write alerts for actionable signals (24h cooldown per signal+shop)
    3. Generate improvement insights
    4. Return summary

    Called by agent_worker every 15 minutes.
    """
    from app.services.alerting import write_alert

    friction_signals = detect_friction(db)
    insights = generate_insights(db)

    # Write alerts for warning-level friction signals (24h per-shop cooldown)
    alerts_written = 0
    alerts_suppressed = 0
    for sig in friction_signals:
        if sig["severity"] in ("warning", "critical"):
            if _friction_alert_suppressed(db, sig["signal"], sig["shop_domain"]):
                alerts_suppressed += 1
                continue
            # heal-detection: 24h per-shop cooldown via ops_alerts dedup window — recovery = shop unblocks within 24h (next scan finds no friction)
            write_alert(
                db,
                source="onboarding_funnel",
                alert_type=sig["signal"],
                severity=sig["severity"],
                shop_domain=sig["shop_domain"],
                summary=sig["detail"],
                detail={"signal": sig["signal"]},
            )
            alerts_written += 1

    # Write alerts for high-severity insights (aggregate, 24h cooldown)
    for ins in insights:
        if ins["severity"] == "high":
            alert_type = f"insight_{ins['insight']}"
            if _friction_alert_suppressed(db, alert_type, None):
                alerts_suppressed += 1
                continue
            write_alert(
                db,
                source="onboarding_funnel",
                alert_type=alert_type,
                severity="warning",  # promote to warning so it's visible but not noise
                summary=f"{ins['metric']} — {ins['suggestion']}",
                detail=ins,
            )
            alerts_written += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        log.exception("onboarding_funnel: failed to commit friction detection results")

    return {
        "friction_signals": len(friction_signals),
        "insights": len(insights),
        "alerts_written": alerts_written,
        "alerts_suppressed": alerts_suppressed,
        "signals_by_type": _count_by_key(friction_signals, "signal"),
        "top_insights": [i["insight"] for i in insights[:5]],
    }


def _count_by_key(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        v = item.get(key, "unknown")
        counts[v] = counts.get(v, 0) + 1
    return counts
