"""
action_learning.py — Measure action outcomes and feed learning back.

After a nudge is created from an ActionTask, this module:
    1. Waits 48 hours for measurement data to accumulate
    2. Reads nudge_measurement attribution (exposed vs holdout)
    3. Determines outcome: success / no_effect / degraded
    4. Updates ActionOutcome record
    5. Feeds effectiveness data back to action_candidates_engine
       (via action_outcome records that the ranking formula reads)

Learning loop:
    action_candidates_engine reads ActionOutcome.outcome_status to compute
    effectiveness_boost in the ranking formula. Actions with high success
    rates get ranked higher. Actions with repeated no_effect get deprioritized.

Called by: agent_worker.py (after action_agent cycle)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.action_outcome import ActionOutcome

log = logging.getLogger("action_learning")

_MEASUREMENT_WINDOW_HOURS = 48
_MIN_EXPOSED_SAMPLE = 5  # Need at least 5 exposed visitors to judge


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def evaluate_pending_outcomes(db: Session) -> dict:
    """
    Evaluate pending action outcomes that have had enough time to accumulate data.

    Returns {"evaluated": int, "success": int, "no_effect": int, "insufficient_data": int}
    """
    summary = {"evaluated": 0, "success": 0, "no_effect": 0, "insufficient_data": 0}

    cutoff = _now() - timedelta(hours=_MEASUREMENT_WINDOW_HOURS)

    # Find outcomes ready for evaluation (executed >= 48h ago, still pending)
    pending = (
        db.query(ActionOutcome)
        .filter(
            ActionOutcome.outcome_status == "pending",
            ActionOutcome.executed_at <= cutoff,
        )
        .limit(20)
        .all()
    )

    from app.core.database import savepoint_scope
    for outcome in pending:
        try:
            # SAVEPOINT-per-outcome (write_no_rollback class close
            # 2026-05-19): flush-per-outcome, caller commits — a failing
            # outcome must roll back only itself, not poison the shared
            # session for the remaining outcomes + the caller's commit.
            with savepoint_scope(db):
                _evaluate_one(db, outcome, summary)
                db.flush()
        except Exception as exc:
            log.warning("action_learning: error evaluating outcome %d: %s", outcome.id, exc)

    return summary


def _evaluate_one(db: Session, outcome: ActionOutcome, summary: dict) -> None:
    """Evaluate a single pending outcome using nudge measurement data."""

    # Find the nudge linked to this action (via action_task_id on active_nudges)
    nudge_row = db.execute(text("""
        SELECT an.id, an.shop_domain, an.product_url
        FROM active_nudges an
        JOIN action_tasks at ON an.action_task_id = at.id
        WHERE at.action_type = :atype
          AND at.product_url = :target
          AND at.shop_domain = :shop
        ORDER BY an.created_at DESC
        LIMIT 1
    """), {
        "atype": outcome.action_type,
        "target": outcome.target_id,
        "shop": outcome.shop_domain,
    }).first()

    if not nudge_row:
        # No nudge found — can't measure, mark as unknown
        outcome.outcome_status = "unknown"
        outcome.outcome_detail = "no_linked_nudge"
        outcome.evaluated_at = _now()
        summary["evaluated"] += 1
        return

    nudge_id = nudge_row[0]

    # Check email delivery: was the action recommendation email opened?
    # If merchant never saw the email, outcome should not penalize the action.
    email_opened = _check_email_engagement(db, outcome.shop_domain)
    if email_opened is False:
        outcome.outcome_detail = "email_not_opened"
        # Don't mark as no_effect — that would penalize a good action
        # the merchant simply never saw. Mark as not_delivered.

    # Get measurement data
    stats = _get_nudge_measurement(db, outcome.shop_domain, nudge_id)

    if stats["exposed_count"] < _MIN_EXPOSED_SAMPLE:
        outcome.outcome_status = "unknown"
        outcome.outcome_detail = f"insufficient_data: {stats['exposed_count']} exposed (need {_MIN_EXPOSED_SAMPLE})"
        outcome.evaluated_at = _now()
        summary["insufficient_data"] += 1
        summary["evaluated"] += 1
        return

    # Determine outcome based on exposed vs holdout conversion
    if stats["holdout_count"] == 0:
        # No holdout data — use before/after comparison
        if stats["exposed_conversions"] > 0:
            verdict = "success"
        else:
            verdict = "no_effect"
    else:
        # Holdout comparison (quasi-experimental)
        exposed_rate = stats["exposed_conversions"] / max(1, stats["exposed_count"])
        holdout_rate = stats["holdout_conversions"] / max(1, stats["holdout_count"])

        lift = exposed_rate - holdout_rate

        if lift > 0.01:  # >1% absolute lift = success
            verdict = "success"
        elif lift < -0.01:  # Negative lift = degraded
            verdict = "degraded"
        else:
            verdict = "no_effect"

    # Adjust verdict if merchant never opened emails (can't attribute outcome to action)
    if verdict == "no_effect" and email_opened is False:
        verdict = "not_delivered"

    outcome.outcome_status = verdict
    email_ctx = f", email_opened={email_opened}" if email_opened is not None else ""
    outcome.outcome_detail = (
        f"exposed={stats['exposed_count']}, holdout={stats['holdout_count']}, "
        f"exposed_cvr={stats['exposed_conversions']}/{stats['exposed_count']}, "
        f"holdout_cvr={stats['holdout_conversions']}/{stats['holdout_count']}"
        f"{email_ctx}"
    )
    outcome.evaluated_at = _now()

    summary["evaluated"] += 1
    summary[verdict] = summary.get(verdict, 0) + 1

    log.info(
        "action_learning: outcome=%s for %s/%s (exposed=%d, holdout=%d, verdict=%s)",
        outcome.id, outcome.shop_domain, outcome.target_id,
        stats["exposed_count"], stats["holdout_count"], verdict,
    )


def _get_nudge_measurement(db: Session, shop: str, nudge_id: int) -> dict:
    """Get exposed/holdout measurement data for a nudge."""

    # Count exposed (shown) visitors
    exposed = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM nudge_events
        WHERE shop_domain = :shop AND nudge_id = :nid AND event_type = 'shown'
    """), {"shop": shop, "nid": nudge_id}).scalar() or 0

    # Count holdout visitors
    holdout = db.execute(text("""
        SELECT COUNT(DISTINCT visitor_id)
        FROM nudge_events
        WHERE shop_domain = :shop AND nudge_id = :nid AND event_type = 'holdout_assigned'
    """), {"shop": shop, "nid": nudge_id}).scalar() or 0

    # Count exposed conversions (visitors who saw nudge AND purchased)
    exposed_conv = db.execute(text("""
        SELECT COUNT(DISTINCT ne.visitor_id)
        FROM nudge_events ne
        JOIN visitor_purchase_sessions vps
          ON vps.shop_domain = ne.shop_domain AND vps.visitor_id = ne.visitor_id
        WHERE ne.shop_domain = :shop AND ne.nudge_id = :nid AND ne.event_type = 'shown'
    """), {"shop": shop, "nid": nudge_id}).scalar() or 0

    # Count holdout conversions
    holdout_conv = db.execute(text("""
        SELECT COUNT(DISTINCT ne.visitor_id)
        FROM nudge_events ne
        JOIN visitor_purchase_sessions vps
          ON vps.shop_domain = ne.shop_domain AND vps.visitor_id = ne.visitor_id
        WHERE ne.shop_domain = :shop AND ne.nudge_id = :nid AND ne.event_type = 'holdout_assigned'
    """), {"shop": shop, "nid": nudge_id}).scalar() or 0

    return {
        "exposed_count": exposed,
        "holdout_count": holdout,
        "exposed_conversions": exposed_conv,
        "holdout_conversions": holdout_conv,
    }


def _check_email_engagement(db: Session, shop_domain: str) -> bool | None:
    """
    Check if merchant opened ANY email in the last 7 days.

    Returns:
        True  — merchant is email-engaged (opened at least one)
        False — merchant received emails but opened none
        None  — no email data available (can't determine)

    This closes the intelligence loop: if a merchant never opens emails,
    action outcomes should not be attributed to "bad action" — the merchant
    simply never saw the recommendation.
    """
    try:
        row = db.execute(text("""
            SELECT
                COALESCE(SUM(sent_count), 0) AS total_sent,
                COALESCE(SUM(opened_count), 0) AS total_opened
            FROM merchant_email_stats
            WHERE shop_domain = :shop
        """), {"shop": shop_domain}).first()

        if not row or row[0] == 0:
            return None  # no email data
        return row[1] > 0  # True if any opens
    except Exception as exc:
        log.warning(
            "action_learning: email open probe failed shop=%s (%s): %s",
            shop_domain, type(exc).__name__, str(exc)[:200],
        )
        return None  # can't determine
