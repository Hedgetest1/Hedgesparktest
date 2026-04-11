"""
action_agent.py — Autonomous action execution agent.

Closes the loop: opportunity_signals → action_candidates → ActionTask → execution.

For each pending ActionTask:
    1. Claim it atomically (SELECT FOR UPDATE)
    2. Determine if auto-executable or needs approval
    3. For auto-executable tasks: create/refresh nudge immediately
    4. For approval-required tasks: create ActionApproval, notify operator
    5. Report outcome (done/failed)

Risk levels:
    AUTO (TIER_0):
        - SCARCITY_NUDGE: Creates storefront nudge with A/B + holdout
        - RETARGET_HOT_TRAFFIC: Creates "return visitor" nudge variant
    APPROVAL (TIER_1):
        - CRO_FIX: Suggests page changes (merchant must act)
        - PRICE_TEST: Suggests compare-at price change
        - FLASH_INCENTIVE: Time-limited offer (merchant must approve)

Learning:
    After execution, outcomes are measured by nudge_measurement.
    action_outcome records feed back into action_candidates_engine ranking
    (effectiveness_boost in ranking formula).

Called by: agent_worker.py (new phase)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.action_task import ActionTask

log = logging.getLogger("action_agent")

AGENT_ID = "action_agent_v1"

# Action types that can be auto-executed as nudges
_AUTO_EXECUTABLE_TYPES = {"SCARCITY_NUDGE", "RETARGET_HOT_TRAFFIC"}

# Action types that require merchant/operator approval
_APPROVAL_REQUIRED_TYPES = {"CRO_FIX", "PRICE_TEST", "FLASH_INCENTIVE"}

_MAX_TASKS_PER_CYCLE = 10
_MAX_NUDGES_PER_MERCHANT_PER_DAY = 3  # prevent single merchant from dominating cycle
_REDIS_NUDGE_CAP_PREFIX = "hs:nudge_cap:"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_action_cycle(db: Session) -> dict:
    """
    Process pending action tasks.

    Returns {"claimed": int, "executed": int, "approval_queued": int,
             "failed": int, "skipped": int}
    """
    summary = {"claimed": 0, "executed": 0, "approval_queued": 0,
               "failed": 0, "skipped": 0}

    # Find pending tasks, ordered by urgency
    pending = (
        db.query(ActionTask)
        .filter(ActionTask.status == "pending")
        .order_by(ActionTask.urgency.desc().nullslast())
        .limit(_MAX_TASKS_PER_CYCLE)
        .all()
    )

    if not pending:
        return summary

    for task in pending:
        try:
            _process_task(db, task, summary)
            db.flush()
        except Exception as exc:
            log.error("action_agent: error on task %d: %s", task.id, exc)
            summary["failed"] += 1
            db.rollback()

    return summary


def _process_task(db: Session, task: ActionTask, summary: dict) -> None:
    """Process a single pending task."""
    from app.services.action_executor import claim_task, transition_task

    # Claim atomically
    claimed, err = claim_task(db, task.id, task.shop_domain, AGENT_ID)
    if not claimed:
        log.info("action_agent: skip task %d — %s", task.id, err)
        summary["skipped"] += 1
        return

    summary["claimed"] += 1
    action_type = task.action_type

    if action_type in _AUTO_EXECUTABLE_TYPES:
        # Per-merchant daily nudge cap
        if not _check_nudge_cap(task.shop_domain):
            log.info("action_agent: daily nudge cap reached for %s, deferring task %d", task.shop_domain, task.id)
            summary["skipped"] += 1
            return

        # Auto-execute: create nudge
        success = _execute_nudge_action(db, task)
        import json as _json
        if success:
            transition_task(db, task, "done", _json.dumps({
                "outcome": "PASS",
                "agent_id": AGENT_ID,
                "summary": f"Nudge created for {task.product_url}",
            }))
            summary["executed"] += 1

            # Create outcome record for measurement
            _create_outcome(db, task)
        else:
            transition_task(db, task, "failed", _json.dumps({
                "outcome": "ERROR",
                "agent_id": AGENT_ID,
                "summary": "Nudge creation failed",
            }))
            summary["failed"] += 1

    elif action_type in _APPROVAL_REQUIRED_TYPES:
        # Queue for approval
        _queue_for_approval(db, task)
        summary["approval_queued"] += 1

    else:
        log.warning("action_agent: unknown action type %s on task %d", action_type, task.id)
        summary["skipped"] += 1


def _execute_nudge_action(db: Session, task: ActionTask) -> bool:
    """Create a live nudge from an action task. Returns True on success."""
    from app.services.nudge_engine import create_or_refresh_nudge

    # Extract context from task payload
    payload = task.task_payload or {}
    segment_ctx = payload.get("segment_context", {})

    visitor_count = segment_ctx.get("visitor_count", 0)
    revenue_window = segment_ctx.get("estimated_revenue_window")
    calibration_state = segment_ctx.get("calibration_state")

    try:
        nudge, created = create_or_refresh_nudge(
            db=db,
            shop_domain=task.shop_domain,
            product_url=task.product_url,
            action_type=task.action_type,
            trigger_source=AGENT_ID,
            visitor_count=visitor_count,
            revenue_window=revenue_window,
            calibration_state=calibration_state,
            action_task_id=task.id,
            holdout_pct=20,  # Always use 20% holdout for measurement
        )

        log.info(
            "action_agent: nudge %s for %s/%s (task=%d, holdout=20%%)",
            "created" if created else "refreshed",
            task.shop_domain, task.product_url, task.id,
        )
        return True

    except Exception as exc:
        log.error(
            "action_agent: nudge creation failed task=%d shop=%s: %s",
            task.id, task.shop_domain, exc,
        )
        return False


def _queue_for_approval(db: Session, task: ActionTask) -> None:
    """Create an approval request and notify operator."""
    from app.services.audit import write_audit_log
    from app.models.action_approval import ActionApproval

    # Write audit log entry
    audit = write_audit_log(
        db,
        actor_type="agent",
        actor_name=AGENT_ID,
        action_type=f"propose_{task.action_type.lower()}",
        target_type="product",
        target_id=task.product_url,
        shop_domain=task.shop_domain,
        status="pending",
        approval_mode="human_required",
    )
    db.flush()

    # Create approval request
    approval = ActionApproval(
        audit_log_id=audit.id,
        action_type=task.action_type,
        target_id=task.product_url,
        shop_domain=task.shop_domain,
        status="pending",
        expires_at=_now() + __import__("datetime").timedelta(hours=48),
    )
    db.add(approval)
    db.flush()

    # Notify operator
    try:
        from app.core.alert_delivery import notify_approval_pending
        notify_approval_pending(
            approval_id=approval.id,
            action_type=task.action_type,
            target_id=task.product_url,
            shop_domain=task.shop_domain,
            reason=_summarize_task(task),
        )
    except Exception as exc:
        log.warning("action_agent: approval notification failed: %s", exc)

    # Transition task to awaiting approval (keep as executing, agent still owns it)
    task.result_detail = f'{{"status": "awaiting_approval", "approval_id": {approval.id}}}'
    db.flush()

    log.info(
        "action_agent: approval queued for %s on %s (task=%d, approval=%d)",
        task.action_type, task.product_url, task.id, approval.id,
    )


def _create_outcome(db: Session, task: ActionTask) -> None:
    """Create an outcome record for post-execution measurement."""
    from app.models.action_outcome import ActionOutcome

    # Check for existing outcome
    existing = (
        db.query(ActionOutcome.id)
        .filter(
            ActionOutcome.action_type == task.action_type,
            ActionOutcome.target_id == task.product_url,
            ActionOutcome.shop_domain == task.shop_domain,
            ActionOutcome.outcome_status == "pending",
        )
        .first()
    )
    if existing:
        return

    outcome = ActionOutcome(
        audit_log_id=0,  # No audit log for auto-executed actions
        action_type=task.action_type,
        target_id=task.product_url,
        shop_domain=task.shop_domain,
        executed_at=_now(),
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()


def _summarize_task(task: ActionTask) -> str:
    """Build a short human-readable summary for operator notification."""
    payload = task.task_payload or {}
    fixes = payload.get("suggested_fixes", [])
    if fixes:
        top_fix = fixes[0].get("fix", "No fix details")
        return f"{task.action_type}: {top_fix}"
    return f"{task.action_type} on {task.product_url}"


def _check_nudge_cap(shop_domain: str) -> bool:
    """Check per-merchant daily nudge creation cap. Returns True if under cap."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            return True  # fail-open
        key = f"{_REDIS_NUDGE_CAP_PREFIX}{shop_domain}"
        count = rc.get(key)
        if count is not None and int(count) >= _MAX_NUDGES_PER_MERCHANT_PER_DAY:
            return False
        pipe = rc.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, 86400)
        pipe.execute()
        return True
    except Exception:
        return True  # fail-open
