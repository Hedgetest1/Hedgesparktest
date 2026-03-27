"""Tests for outcome intelligence in orchestrator context builder."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.action_outcome import ActionOutcome
from app.models.merchant import Merchant
from app.services.orchestrator_context import (
    build_orchestrator_context,
    _build_outcomes_section,
    _MAX_OUTCOME_ACTION_TYPES,
)
from tests.conftest import SHOP_A


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_outcomes(db, action_type, successes, no_effects, unknowns):
    """Seed evaluated outcomes for testing."""
    base_time = _now() - timedelta(hours=2)
    audit_id = 1000
    for i in range(successes):
        db.add(ActionOutcome(
            audit_log_id=audit_id, action_type=action_type, target_id="t",
            executed_at=base_time - timedelta(minutes=i),
            evaluated_at=_now(), outcome_status="success",
        ))
        audit_id += 1
    for i in range(no_effects):
        db.add(ActionOutcome(
            audit_log_id=audit_id, action_type=action_type, target_id="t",
            executed_at=base_time - timedelta(minutes=successes + i),
            evaluated_at=_now(), outcome_status="no_effect",
        ))
        audit_id += 1
    for i in range(unknowns):
        db.add(ActionOutcome(
            audit_log_id=audit_id, action_type=action_type, target_id="t",
            executed_at=base_time - timedelta(minutes=successes + no_effects + i),
            evaluated_at=_now(), outcome_status="unknown",
        ))
        audit_id += 1
    db.flush()


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------

def test_outcomes_section_correct_counts(db):
    """Outcome section shows correct counts per action type."""
    _seed_outcomes(db, "orch_webhook_repair", successes=3, no_effects=2, unknowns=0)
    section = _build_outcomes_section(db, _now())

    assert "webhook_repair" in section
    assert "executions=5" in section
    assert "success=3" in section
    assert "no_effect=2" in section


def test_success_rate_calculation(db):
    """Success rate is correctly calculated."""
    _seed_outcomes(db, "orch_resolve_alert", successes=7, no_effects=1, unknowns=2)
    section = _build_outcomes_section(db, _now())

    assert "resolve_alert" in section
    assert "executions=10" in section
    assert "success=7" in section
    assert "success_rate=70%" in section


def test_zero_executions_safe(db):
    """No outcomes → clean 'no evaluated outcomes' message."""
    section = _build_outcomes_section(db, _now())
    assert "No evaluated outcomes" in section


def test_pending_excluded(db):
    """Pending outcomes are NOT counted in aggregation."""
    db.add(ActionOutcome(
        audit_log_id=2000, action_type="orch_webhook_repair", target_id="t",
        executed_at=_now(), outcome_status="pending",
    ))
    db.flush()

    section = _build_outcomes_section(db, _now())
    assert "No evaluated outcomes" in section


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_truncation_at_max_types(db):
    """Only top N action types by volume are shown."""
    for i in range(_MAX_OUTCOME_ACTION_TYPES + 3):
        _seed_outcomes(db, f"orch_action_{i}", successes=1, no_effects=0, unknowns=0)

    section = _build_outcomes_section(db, _now())
    # Count lines that start with "  " (action lines)
    action_lines = [l for l in section.split("\n") if l.startswith("  ") and "executions=" in l]
    assert len(action_lines) <= _MAX_OUTCOME_ACTION_TYPES


# ---------------------------------------------------------------------------
# Full context integration
# ---------------------------------------------------------------------------

def test_full_context_includes_outcomes(db, merchant_a):
    """build_orchestrator_context includes Action Outcomes section."""
    _seed_outcomes(db, "orch_webhook_repair", successes=2, no_effects=1, unknowns=0)
    context = build_orchestrator_context(db)

    assert "## Action Outcomes" in context
    assert "webhook_repair" in context
    assert "success_rate=" in context


def test_full_context_still_has_other_sections(db, merchant_a):
    """Adding outcomes doesn't break existing sections."""
    context = build_orchestrator_context(db)
    assert "## Alerts" in context
    assert "## Workers" in context
    assert "## System Vitals" in context
    assert "## Action Outcomes" in context


# ---------------------------------------------------------------------------
# 100% success rate edge case
# ---------------------------------------------------------------------------

def test_100_percent_success_rate(db):
    """All successes → 100% rate."""
    _seed_outcomes(db, "orch_resolve_alert", successes=5, no_effects=0, unknowns=0)
    section = _build_outcomes_section(db, _now())
    assert "success_rate=100%" in section


def test_0_percent_success_rate(db):
    """All failures → 0% rate."""
    _seed_outcomes(db, "orch_webhook_repair", successes=0, no_effects=4, unknowns=1)
    section = _build_outcomes_section(db, _now())
    assert "success_rate=0%" in section
