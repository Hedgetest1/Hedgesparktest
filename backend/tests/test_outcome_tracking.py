"""Tests for outcome tracking and feedback loop."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from app.models.action_outcome import ActionOutcome
from app.services.outcome_evaluator import (
    record_pending_outcome,
    evaluate_pending_outcomes,
    count_recent_failures,
    MIN_EVAL_DELAY_SECONDS,
)
from app.services.orchestrator import (
    _evaluate_decisions,
    _clear_cooldowns,
    run_orchestrator_cycle,
)
from tests.conftest import SHOP_A


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clear_pending_outcomes(db):
    """Mark all pre-existing pending outcomes as evaluated so they don't interfere."""
    db.query(ActionOutcome).filter(ActionOutcome.outcome_status == "pending").update(
        {"outcome_status": "unknown", "evaluated_at": _now()},
        synchronize_session="fetch",
    )
    db.flush()


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------

def test_record_pending_outcome(db):
    """record_pending_outcome creates a pending row."""
    outcome = record_pending_outcome(
        db,
        audit_log_id=999,
        action_type="orch_webhook_repair",
        target_id=SHOP_A,
        shop_domain=SHOP_A,
    )
    assert outcome.id is not None
    assert outcome.outcome_status == "pending"
    assert outcome.audit_log_id == 999


# ---------------------------------------------------------------------------
# Outcome evaluation
# ---------------------------------------------------------------------------

def test_evaluate_resolve_alert_success(db):
    """Resolved alert that stays resolved → success."""
    _clear_pending_outcomes(db)
    # Create and resolve an alert
    alert = OpsAlert(
        severity="info", source="test", alert_type="test_eval",
        summary="eval test", created_at=_now(),
    )
    db.add(alert)
    db.flush()
    alert.resolved = True
    db.flush()

    # Create pending outcome (old enough to evaluate)
    outcome = ActionOutcome(
        audit_log_id=1,
        action_type="orch_resolve_alert",
        target_id=str(alert.id),
        executed_at=_now() - timedelta(seconds=MIN_EVAL_DELAY_SECONDS + 60),
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()

    result = evaluate_pending_outcomes(db)
    assert result.evaluated == 1
    assert result.success == 1

    db.refresh(outcome)
    assert outcome.outcome_status == "success"
    assert outcome.evaluated_at is not None


def test_evaluate_resolve_alert_no_effect(db):
    """Alert not actually resolved → no_effect."""
    _clear_pending_outcomes(db)
    alert = OpsAlert(
        severity="info", source="test", alert_type="test_no_effect",
        summary="still broken", created_at=_now(),
    )
    db.add(alert)
    db.flush()
    # NOT resolved — outcome should be no_effect

    outcome = ActionOutcome(
        audit_log_id=2,
        action_type="orch_resolve_alert",
        target_id=str(alert.id),
        executed_at=_now() - timedelta(seconds=MIN_EVAL_DELAY_SECONDS + 60),
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()

    result = evaluate_pending_outcomes(db)
    assert result.no_effect == 1

    db.refresh(outcome)
    assert outcome.outcome_status == "no_effect"


def test_too_recent_not_evaluated(db):
    """Outcome executed just now → not evaluated yet."""
    _clear_pending_outcomes(db)
    outcome = ActionOutcome(
        audit_log_id=3,
        action_type="orch_resolve_alert",
        target_id="999",
        executed_at=_now(),  # just now
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()

    result = evaluate_pending_outcomes(db)
    assert result.evaluated == 0

    db.refresh(outcome)
    assert outcome.outcome_status == "pending"


def test_unknown_action_type_evaluates_as_unknown(db):
    """Action type with no registered evaluator → unknown."""
    outcome = ActionOutcome(
        audit_log_id=4,
        action_type="orch_restart_server",  # no evaluator registered
        target_id="test",
        executed_at=_now() - timedelta(seconds=MIN_EVAL_DELAY_SECONDS + 60),
        outcome_status="pending",
    )
    db.add(outcome)
    db.flush()

    result = evaluate_pending_outcomes(db)
    assert result.unknown == 1


# ---------------------------------------------------------------------------
# Feedback into orchestrator decisions
# ---------------------------------------------------------------------------

def test_orchestrator_skips_repeated_failures(db, merchant_a):
    """If webhook_repair had 2+ no_effect outcomes, orchestrator skips it."""
    _clear_cooldowns()

    # Create the webhook_repair_failed alert
    alert = OpsAlert(
        severity="warning", source="test",
        alert_type="webhook_repair_failed",
        shop_domain=SHOP_A, summary="repair failed",
        created_at=_now(),
    )
    db.add(alert)
    db.flush()

    # Record 2 failed outcomes for this target
    for i in range(2):
        db.add(ActionOutcome(
            audit_log_id=100 + i,
            action_type="orch_webhook_repair",
            target_id=SHOP_A,
            shop_domain=SHOP_A,
            executed_at=_now() - timedelta(hours=i),
            evaluated_at=_now() - timedelta(hours=i),
            outcome_status="no_effect",
            outcome_detail="still_missing",
        ))
    db.flush()

    # Evaluate decisions — should skip webhook_repair for this shop
    candidates = _evaluate_decisions(db)
    webhook_candidates = [c for c in candidates if c.action == "webhook_repair" and c.target == SHOP_A]
    assert len(webhook_candidates) == 0


def test_count_recent_failures(db):
    """count_recent_failures returns correct count."""
    for i in range(3):
        db.add(ActionOutcome(
            audit_log_id=200 + i,
            action_type="orch_webhook_repair",
            target_id=SHOP_A,
            executed_at=_now() - timedelta(hours=i),
            evaluated_at=_now(),
            outcome_status="no_effect",
        ))
    db.flush()

    count = count_recent_failures(db, "orch_webhook_repair", SHOP_A, hours=24)
    assert count == 3


# ---------------------------------------------------------------------------
# Integration: orchestrator records outcomes
# ---------------------------------------------------------------------------

def test_orchestrator_cycle_records_outcome(db, merchant_a):
    """Executed deterministic action creates a pending outcome row."""
    _clear_cooldowns()

    alert = OpsAlert(
        severity="info", source="test", alert_type="webhook_repaired",
        summary="old", created_at=_now() - timedelta(hours=5),
    )
    db.add(alert)
    db.flush()

    result = run_orchestrator_cycle(db)
    assert result.actions_executed >= 1

    # Check outcome was recorded
    outcomes = db.execute(text(
        "SELECT outcome_status, action_type FROM action_outcomes ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert outcomes is not None
    assert outcomes[0] == "pending"
    assert "resolve_alert" in outcomes[1]
