"""
outcome_evaluator.py — Evaluates whether orchestrator actions improved system state.

Runs AFTER the orchestrator cycle (next cycle or later), checks pending
outcomes, and classifies them as success/no_effect/degraded/unknown.

Each action type has a specific evaluator function that checks current
system state against what the action was supposed to fix.

Public interface:
    record_pending_outcome(db, audit_log_id, ...) -> ActionOutcome
    evaluate_pending_outcomes(db) -> EvaluationResult
    get_recent_outcomes(db, action_type, ...) -> list[ActionOutcome]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.action_outcome import ActionOutcome

log = logging.getLogger("outcome_evaluator")

# Minimum time after execution before evaluating (let the action take effect)
MIN_EVAL_DELAY_SECONDS = 600  # 10 minutes


@dataclass
class EvaluationResult:
    evaluated: int = 0
    success: int = 0
    no_effect: int = 0
    degraded: int = 0
    unknown: int = 0


# ---------------------------------------------------------------------------
# Record a pending outcome (called immediately after action execution)
# ---------------------------------------------------------------------------

def record_pending_outcome(
    db: Session,
    *,
    audit_log_id: int,
    action_type: str,
    target_id: str | None = None,
    shop_domain: str | None = None,
    executed_at: datetime | None = None,
) -> ActionOutcome:
    """Create a pending outcome record for a newly executed action."""
    outcome = ActionOutcome(
        audit_log_id=audit_log_id,
        action_type=action_type,
        target_id=target_id,
        shop_domain=shop_domain,
        executed_at=executed_at or _now(),
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()
    return outcome


# ---------------------------------------------------------------------------
# Evaluate pending outcomes (called by agent_worker on subsequent cycles)
# ---------------------------------------------------------------------------

def evaluate_pending_outcomes(db: Session) -> EvaluationResult:
    """
    Find all pending outcomes that are old enough to evaluate,
    run the appropriate evaluator, and update their status.
    """
    result = EvaluationResult()
    cutoff = _now() - timedelta(seconds=MIN_EVAL_DELAY_SECONDS)

    pending = (
        db.query(ActionOutcome)
        .filter(
            ActionOutcome.outcome_status == "pending",
            ActionOutcome.executed_at <= cutoff,
        )
        .order_by(ActionOutcome.executed_at)
        .limit(20)
        .all()
    )

    for outcome in pending:
        evaluator = _EVALUATORS.get(outcome.action_type)
        if evaluator is None:
            outcome.outcome_status = "unknown"
            outcome.outcome_detail = "no_evaluator_registered"
            outcome.evaluated_at = _now()
            result.unknown += 1
        else:
            try:
                status, detail = evaluator(db, outcome)
                outcome.outcome_status = status
                outcome.outcome_detail = detail
                outcome.evaluated_at = _now()
                getattr(result, status, None)  # validate status name
                if status == "success":
                    result.success += 1
                elif status == "no_effect":
                    result.no_effect += 1
                elif status == "degraded":
                    result.degraded += 1
                else:
                    result.unknown += 1
            except Exception as exc:
                outcome.outcome_status = "unknown"
                outcome.outcome_detail = f"evaluator_error: {str(exc)[:200]}"
                outcome.evaluated_at = _now()
                result.unknown += 1
                log.warning("outcome_evaluator: error evaluating %s: %s", outcome.action_type, exc)

        result.evaluated += 1

    if result.evaluated > 0:
        db.flush()
        log.info(
            "outcome_evaluator: evaluated=%d success=%d no_effect=%d degraded=%d unknown=%d",
            result.evaluated, result.success, result.no_effect, result.degraded, result.unknown,
        )

    return result


# ---------------------------------------------------------------------------
# Per-action evaluators
# ---------------------------------------------------------------------------

def _eval_webhook_repair(db: Session, outcome: ActionOutcome) -> tuple[str, str]:
    """Check if webhook is now healthy for the target shop."""
    shop = outcome.target_id or outcome.shop_domain
    if not shop:
        return "unknown", "no_target_shop"

    from app.services.webhook_health import check_webhook_health
    report = check_webhook_health(db, shop)

    if report.error:
        return "unknown", f"health_check_error: {report.error}"
    if report.healthy:
        return "success", "webhooks_healthy"
    if report.missing:
        return "no_effect", f"still_missing: {report.missing}"
    if report.stale:
        return "no_effect", f"still_stale: {report.stale}"
    return "unknown", "inconclusive"


def _eval_resolve_alert(db: Session, outcome: ActionOutcome) -> tuple[str, str]:
    """Check if the resolved alert stayed resolved or was re-created."""
    from app.models.ops_alert import OpsAlert

    # The alert itself should still be resolved
    try:
        alert_id = int(outcome.target_id)
    except (ValueError, TypeError):
        return "unknown", "invalid_alert_id"

    alert = db.get(OpsAlert, alert_id)
    if alert is None:
        return "success", "alert_deleted_or_not_found"
    if alert.resolved:
        # Check if a NEW unresolved alert of the same type was created since
        same_type = (
            db.query(OpsAlert)
            .filter(
                OpsAlert.alert_type == alert.alert_type,
                OpsAlert.resolved == False,
                OpsAlert.created_at > outcome.executed_at,
            )
        )
        if alert.shop_domain:
            same_type = same_type.filter(OpsAlert.shop_domain == alert.shop_domain)
        if same_type.first():
            return "no_effect", "alert_resolved_but_same_type_recreated"
        return "success", "alert_resolved_and_stable"
    return "no_effect", "alert_not_resolved"


def _eval_clear_redis_cache(db: Session, outcome: ActionOutcome) -> tuple[str, str]:
    """Check if cache clear resolved the issue (no new cache alerts)."""
    from app.models.ops_alert import OpsAlert
    # If no new cache-related alerts since the action, it's a success
    new_alerts = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type.in_(["cache_error", "redis_error"]),
            OpsAlert.created_at > outcome.executed_at,
            OpsAlert.resolved == False,
        )
        .count()
    )
    if new_alerts == 0:
        return "success", "no_new_cache_alerts"
    return "no_effect", f"cache_alerts_persist: {new_alerts}"


def _eval_restart_worker(db: Session, outcome: ActionOutcome) -> tuple[str, str]:
    """Check if worker returned to healthy state after restart."""
    worker_name = outcome.target_id
    if not worker_name:
        return "unknown", "no_target_worker"

    # Check worker_log for recent cycles without errors
    from datetime import timedelta
    cutoff = outcome.executed_at + timedelta(minutes=5)
    rows = db.execute(text("""
        SELECT errors FROM worker_log
        WHERE worker_name = :wn AND started_at >= :cutoff
        ORDER BY started_at DESC LIMIT 3
    """), {"wn": worker_name.replace("wishspark-", "").replace("-", "_"), "cutoff": cutoff}).fetchall()

    if not rows:
        return "unknown", "no_cycles_since_restart"
    error_cycles = sum(1 for r in rows if r[0] and r[0] > 0)
    if error_cycles == 0:
        return "success", f"worker_healthy ({len(rows)} clean cycles)"
    return "no_effect", f"worker_still_erroring ({error_cycles}/{len(rows)} cycles)"


def _eval_db_connection_reset(db: Session, outcome: ActionOutcome) -> tuple[str, str]:
    """Check if DB connection errors stopped after pool reset."""
    try:
        db.execute(text("SELECT 1"))
        return "success", "db_connection_ok"
    except Exception as exc:
        return "no_effect", f"db_still_failing: {type(exc).__name__}"


# Evaluator registry: action_type_prefix → evaluator function
# Matches the action_type stored in audit_log (e.g. "orch_webhook_repair")
_EVALUATORS: dict[str, callable] = {
    "orch_webhook_repair": _eval_webhook_repair,
    "orch_resolve_alert": _eval_resolve_alert,
    "orch_clear_redis_cache": _eval_clear_redis_cache,
    "orch_restart_worker": _eval_restart_worker,
    "orch_db_connection_reset": _eval_db_connection_reset,
    "llm_exec_webhook_repair": _eval_webhook_repair,
    "llm_exec_resolve_alert": _eval_resolve_alert,
    "llm_exec_clear_redis_cache": _eval_clear_redis_cache,
    "llm_exec_restart_worker": _eval_restart_worker,
    "llm_exec_db_connection_reset": _eval_db_connection_reset,
}


# ---------------------------------------------------------------------------
# Query helpers for orchestrator feedback
# ---------------------------------------------------------------------------

def get_recent_outcomes(
    db: Session,
    action_type: str | None = None,
    target_id: str | None = None,
    hours: int = 24,
) -> list[ActionOutcome]:
    """Get recent evaluated outcomes, optionally filtered."""
    cutoff = _now() - timedelta(hours=hours)
    q = db.query(ActionOutcome).filter(ActionOutcome.executed_at >= cutoff)
    if action_type:
        q = q.filter(ActionOutcome.action_type == action_type)
    if target_id:
        q = q.filter(ActionOutcome.target_id == target_id)
    return q.order_by(ActionOutcome.executed_at.desc()).limit(20).all()


def count_recent_failures(
    db: Session,
    action_type: str,
    target_id: str,
    hours: int = 24,
) -> int:
    """Count recent no_effect outcomes for a specific action+target."""
    cutoff = _now() - timedelta(hours=hours)
    return (
        db.query(ActionOutcome)
        .filter(
            ActionOutcome.action_type == action_type,
            ActionOutcome.target_id == target_id,
            ActionOutcome.outcome_status == "no_effect",
            ActionOutcome.executed_at >= cutoff,
        )
        .count()
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
