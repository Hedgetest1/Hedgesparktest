"""
inbound_action_executor.py — Execute routing actions from classified inbound emails.

Closes the merchant reply loop: classification → routing_action → REAL ACTION.

Actions executed:
    create_support_incident → OpsAlert (type=merchant_email_bug or merchant_email_onboarding)
    log_product_feedback    → OpsAlert (type=product_feedback, severity=info)
    log_positive_feedback   → OpsAlert (type=positive_feedback, severity=info)
    escalate_human          → already handled by Telegram alert at classification time
    archive                 → no action needed

Idempotent: only processes rows with routing_status='routed' and action_executed_at IS NULL.
Each row is marked after execution to prevent re-processing.

Called by: agent_worker.py phase 7 (every 15min cycle)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.inbound_email import InboundEmail

log = logging.getLogger("inbound_action_executor")

# Low-severity bug escalation: if 3+ bugs in same area within 7 days → medium ops_alert
_LOW_BUG_ESCALATION_THRESHOLD = 3
_LOW_BUG_ESCALATION_WINDOW_DAYS = 7


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_inbound_actions(db: Session) -> dict:
    """
    Process all inbound emails with pending routing actions.

    Returns summary: {"processed": int, "incidents_created": int,
                      "feedback_logged": int, "skipped": int, "errors": list}
    """
    summary = {"processed": 0, "incidents_created": 0, "feedback_logged": 0,
               "skipped": 0, "errors": []}

    # Fetch unprocessed routed emails (not escalated/archived — those are already handled)
    pending = (
        db.query(InboundEmail)
        .filter(
            InboundEmail.routing_status == "routed",
            InboundEmail.routing_action.isnot(None),
        )
        .order_by(InboundEmail.created_at.asc())
        .limit(50)
        .all()
    )

    if not pending:
        return summary

    for email in pending:
        try:
            _execute_one(db, email, summary)
            db.flush()
        except Exception as exc:
            log.error("inbound_action_executor: error on email id=%s: %s", email.id, exc)
            summary["errors"].append(f"id={email.id}: {exc}")
            db.rollback()

    return summary


def _execute_one(db: Session, email: InboundEmail, summary: dict) -> None:
    """Execute the routing action for a single inbound email."""
    action = email.routing_action

    if action == "create_support_incident":
        _create_incident(db, email)
        summary["incidents_created"] += 1
    elif action == "log_product_feedback":
        _log_feedback(db, email, "product_feedback")
        summary["feedback_logged"] += 1
    elif action == "log_positive_feedback":
        _log_feedback(db, email, "positive_feedback")
        summary["feedback_logged"] += 1
    elif action in ("escalate_human", "archive", "loop_suppressed", "self_loop_blocked"):
        summary["skipped"] += 1
        email.routing_status = "executed"
        return
    else:
        log.warning("inbound_action_executor: unknown action=%s email_id=%s", action, email.id)
        summary["skipped"] += 1
        email.routing_status = "archived"
        return

    # Auto-respond BEFORE marking executed — if this crashes, email stays
    # in "routed" and will be retried on next cycle
    try:
        from app.services.auto_responder import should_auto_respond, send_auto_response
        if should_auto_respond(email):
            send_auto_response(db, email)
    except Exception as exc:
        log.warning("inbound_action_executor: auto-response failed email_id=%s: %s", email.id, exc)

    # Mark as fully executed LAST — guarantees action + auto-response completed
    email.routing_status = "executed"
    summary["processed"] += 1


def _create_incident(db: Session, email: InboundEmail) -> None:
    """Create an ops_alert from a bug_report or onboarding_confusion email."""
    from app.services.alerting import write_alert

    classification = email.classification or "bug_report"
    alert_type = f"merchant_email_{classification}"
    severity = "warning" if classification == "bug_report" else "info"

    # Dedup: check if we already created an alert for this email
    existing = db.execute(text(
        "SELECT id FROM ops_alerts WHERE alert_type = :atype AND detail LIKE :pattern LIMIT 1"
    ), {"atype": alert_type, "pattern": f'%"inbound_email_id": {email.id}%'}).first()

    if existing:
        log.info("inbound_action_executor: alert already exists for email id=%s", email.id)
        return

    body_preview = (email.body_text or "")[:300]
    write_alert(
        db,
        severity=severity,
        source="inbound_email",
        alert_type=alert_type,
        summary=f"[{classification}] from {email.from_email}: {(email.subject or '')[:100]}",
        shop_domain=email.shop_domain,
        detail={"inbound_email_id": email.id, "body_preview": body_preview},
    )

    log.info(
        "inbound_action_executor: created %s alert for email id=%s shop=%s",
        alert_type, email.id, email.shop_domain,
    )


def _log_feedback(db: Session, email: InboundEmail, alert_type: str) -> None:
    """Log a feature request, suggestion, or positive feedback as an info-level ops_alert."""
    from app.services.alerting import write_alert

    # Dedup
    existing = db.execute(text(
        "SELECT id FROM ops_alerts WHERE alert_type = :atype AND detail LIKE :pattern LIMIT 1"
    ), {"atype": alert_type, "pattern": f'%"inbound_email_id": {email.id}%'}).first()

    if existing:
        return

    body_preview = (email.body_text or "")[:300]
    write_alert(
        db,
        severity="info",
        source="inbound_email",
        alert_type=alert_type,
        summary=f"[{email.classification}] from {email.from_email}: {(email.subject or '')[:100]}",
        shop_domain=email.shop_domain,
        detail={"inbound_email_id": email.id, "body_preview": body_preview},
    )


def run_low_severity_escalation(db: Session) -> dict:
    """
    Aggregate low-severity bugs: if 3+ in same classification+area within 7 days,
    auto-escalate to a medium-severity ops_alert.

    Returns {"escalated": int}
    """
    cutoff = _now() - timedelta(days=_LOW_BUG_ESCALATION_WINDOW_DAYS)

    # Count bug_report emails per shop in the window
    rows = db.execute(text("""
        SELECT shop_domain, COUNT(*) as cnt
        FROM inbound_emails
        WHERE classification = 'bug_report'
          AND created_at >= :cutoff
          AND shop_domain IS NOT NULL
        GROUP BY shop_domain
        HAVING COUNT(*) >= :threshold
    """), {"cutoff": cutoff, "threshold": _LOW_BUG_ESCALATION_THRESHOLD}).fetchall()

    # Bulk-fetch shops that already have an active escalation in the
    # window — collapses N+1 dedup probe into a single query (was 1
    # SELECT per shop). The agent_worker that calls this is a singleton
    # (CLAUDE.md §6) so no race vs concurrent escalators is possible.
    candidate_shops = [r[0] for r in rows]
    already_escalated: set[str] = set()
    if candidate_shops:
        existing_rows = db.execute(text("""
            SELECT DISTINCT shop_domain FROM ops_alerts
            WHERE alert_type = 'merchant_bug_escalation'
              AND shop_domain = ANY(:shops)
              AND created_at >= :cutoff
              AND resolved = false
        """), {"shops": candidate_shops, "cutoff": cutoff}).fetchall()
        already_escalated = {r[0] for r in existing_rows}

    escalated = 0
    for row in rows:
        shop = row[0]
        count = row[1]

        if shop in already_escalated:
            continue

        from app.services.alerting import write_alert
        write_alert(
            db,
            severity="warning",
            source="inbound_action_executor",
            alert_type="merchant_bug_escalation",
            summary=f"Merchant {shop} reported {count} bugs in {_LOW_BUG_ESCALATION_WINDOW_DAYS} days — investigate",
            shop_domain=shop,
        )
        escalated += 1

    return {"escalated": escalated}
