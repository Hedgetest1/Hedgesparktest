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
_APPROVAL_REQUIRED_TYPES = {"PRICE_TEST", "FLASH_INCENTIVE"}

_MAX_TASKS_PER_CYCLE = 10
_MAX_NUDGES_PER_MERCHANT_PER_DAY = 3  # prevent single merchant from dominating cycle
_REDIS_NUDGE_CAP_PREFIX = "hs:nudge_cap:"
_SHOP_BLOCKLIST = frozenset({"legacy.myshopify.com"})


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
        if task.shop_domain in _SHOP_BLOCKLIST:
            log.info("action_agent: skip task %d — blocklisted shop %s", task.id, task.shop_domain)
            task.status = "rejected"
            db.flush()
            summary["skipped"] += 1
            continue
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

    # --- Trust Contract gate: checks quotas, bounds, auto-pause, confidence ---
    # If a contract exists and authorizes this action, we skip the approval
    # queue even for normally-approval-required types. If no contract, we
    # fall back to the original legacy behavior (auto-execute low-risk,
    # queue high-risk).
    from app.services.trust_contract import (
        can_execute as trust_can_execute,
        record_execution as trust_record_execution,
        get_active_contract,
    )

    task_payload = task.task_payload or {}
    task_confidence = float(task.confidence or 0.0)
    task_discount = task_payload.get("discount_pct")
    trust_result = trust_can_execute(
        db,
        shop_domain=task.shop_domain,
        action_type=action_type,
        confidence=task_confidence,
        discount_pct=float(task_discount) if task_discount is not None else None,
        has_holdout=True,  # the system always includes holdout for auto-exec
        target_url=task.product_url,
    )

    if action_type in _AUTO_EXECUTABLE_TYPES:
        # Low-risk path — always try to execute. Trust contract is
        # optional but, if present, contributes to quota tracking.
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
                "trust_contract": trust_result.contract_id,
            }))
            summary["executed"] += 1

            # Create outcome record for measurement
            _create_outcome(db, task)

            # Record under trust contract if one was in force
            if trust_result.allowed and trust_result.contract_id is not None:
                try:
                    contract = get_active_contract(db, task.shop_domain, action_type)
                    if contract is not None:
                        trust_record_execution(
                            db,
                            contract=contract,
                            target_url=task.product_url,
                            confidence=task_confidence,
                            discount_pct=float(task_discount) if task_discount is not None else None,
                            holdout_pct=20,
                            params=task_payload,
                        )
                except Exception as exc:
                    log.warning("action_agent: trust log failed: %s", exc)
        else:
            transition_task(db, task, "failed", _json.dumps({
                "outcome": "ERROR",
                "agent_id": AGENT_ID,
                "summary": "Nudge creation failed",
            }))
            summary["failed"] += 1

    elif action_type in _APPROVAL_REQUIRED_TYPES:
        # High-risk path — approval required UNLESS a trust contract
        # authorizes autonomous execution within guardrails.
        if trust_result.allowed:
            log.info(
                "action_agent: trust contract #%s authorizes %s auto-execute on task %d (remaining today=%s, week=%s)",
                trust_result.contract_id, action_type, task.id,
                trust_result.remaining_today, trust_result.remaining_week,
            )
            success = _execute_high_risk_action_under_trust(db, task, trust_result.contract_id)
            import json as _json
            if success:
                transition_task(db, task, "done", _json.dumps({
                    "outcome": "PASS",
                    "agent_id": AGENT_ID,
                    "summary": f"Autonomous execution under trust contract #{trust_result.contract_id}",
                    "trust_contract": trust_result.contract_id,
                    "approval_mode": "delegated_autonomous",
                }))
                summary["executed"] += 1
                _create_outcome(db, task)
                try:
                    contract = get_active_contract(db, task.shop_domain, action_type)
                    if contract is not None:
                        trust_record_execution(
                            db,
                            contract=contract,
                            target_url=task.product_url,
                            confidence=task_confidence,
                            discount_pct=float(task_discount) if task_discount is not None else None,
                            holdout_pct=20,
                            params=task_payload,
                        )
                except Exception as exc:
                    log.warning("action_agent: trust log failed: %s", exc)
            else:
                transition_task(db, task, "failed", _json.dumps({
                    "outcome": "ERROR",
                    "agent_id": AGENT_ID,
                    "summary": "Trust-authorized execution failed",
                }))
                summary["failed"] += 1
                # Emit triage alert so the pipeline can learn from failed
                # trust-delegated executions.
                try:
                    from app.services.alerting import write_alert
                    write_alert(
                        db,
                        severity="warning",
                        source=f"action_agent:trust:{task.shop_domain}",
                        alert_type="trust_action_failed",
                        summary=(
                            f"Trust-authorized {task.action_type} failed on "
                            f"{task.shop_domain} — contract #{trust_result.contract_id}"
                        ),
                        shop_domain=task.shop_domain,
                        detail={
                            "action_type": task.action_type,
                            "task_id": task.id,
                            "contract_id": trust_result.contract_id,
                        },
                    )
                except Exception as exc:
                    log.warning("action_agent: _process_task failed: %s", exc)
        else:
            log.info(
                "action_agent: no trust for %s on %s (%s) — queuing for approval",
                action_type, task.shop_domain, trust_result.reason,
            )
            _queue_for_approval(db, task)
            summary["approval_queued"] += 1

    else:
        log.warning("action_agent: unknown action type %s on task %d", action_type, task.id)
        summary["skipped"] += 1


def _execute_high_risk_action_under_trust(
    db: Session, task: ActionTask, contract_id: int | None
) -> bool:
    """Execute a PRICE_TEST / FLASH_INCENTIVE task when authorized by a
    trust contract. Currently wires through the same nudge creation path
    as low-risk actions — the high-risk payload (price override, discount
    code, etc.) lives in task.task_payload and is handed to the nudge
    engine so the storefront renders a time-limited banner the visitor
    sees. A future pass can split this into dedicated executors.
    """
    from app.services.nudge_engine import create_or_refresh_nudge

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
            trigger_source=f"{AGENT_ID}:trust_contract:{contract_id}",
            visitor_count=visitor_count,
            revenue_window=revenue_window,
            calibration_state=calibration_state,
            action_task_id=task.id,
            holdout_pct=20,
        )
        log.info(
            "action_agent: trust-authorized nudge %s for %s/%s (task=%d, contract=%s)",
            "created" if created else "refreshed",
            task.shop_domain, task.product_url, task.id, contract_id,
        )
        return True
    except Exception as exc:
        log.error(
            "action_agent: trust-authorized execution failed task=%d shop=%s: %s",
            task.id, task.shop_domain, exc,
        )
        return False


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
    """Check per-merchant daily nudge creation cap. Returns True if under cap.

    Fail-closed: if Redis is unavailable, returns False and records a
    silent-fallback observation so operators can tell when the fast path
    is silently the slow path.
    """
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            record_silent_return("action_agent.nudge_cap")
            log.warning("action_agent: nudge cap — Redis unavailable, fail-closed for %s", shop_domain)
            return False  # fail-closed
        key = f"{_REDIS_NUDGE_CAP_PREFIX}{shop_domain}"
        count = rc.get(key)
        if count is not None and int(count) >= _MAX_NUDGES_PER_MERCHANT_PER_DAY:
            return False
        pipe = rc.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, 86400)
        pipe.execute()
        return True
    except Exception as exc:
        log.warning("action_agent: nudge cap check failed, fail-closed for %s", shop_domain)
        return False  # fail-closed
