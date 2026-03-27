"""Tests for action tiering and hybrid execution mode."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from app.services.orchestrator import (
    ACTION_REGISTRY,
    TIER_0,
    TIER_1,
    TIER_2,
    get_action_tier,
    run_orchestrator_cycle,
    _clear_cooldowns,
)
from app.services.orchestrator_llm import LLMDecisionResult, LLMProposal
from tests.conftest import SHOP_A


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------

def test_known_actions_are_tier_0():
    """Both current actions are TIER_0."""
    assert get_action_tier("webhook_repair") == TIER_0
    assert get_action_tier("resolve_alert") == TIER_0


def test_unknown_action_defaults_to_tier_2():
    """Unknown action name → TIER_2 (highest risk, never auto-execute)."""
    assert get_action_tier("delete_database") == TIER_2
    assert get_action_tier("") == TIER_2


def test_registry_entries_have_three_fields():
    """Every registry entry is (function, description, tier)."""
    for name, entry in ACTION_REGISTRY.items():
        assert len(entry) == 3, f"{name} has {len(entry)} fields, expected 3"
        assert callable(entry[0]), f"{name} function not callable"
        assert isinstance(entry[1], str), f"{name} description not string"
        assert isinstance(entry[2], int), f"{name} tier not int"
        assert entry[2] in (TIER_0, TIER_1, TIER_2), f"{name} tier {entry[2]} invalid"


# ---------------------------------------------------------------------------
# Hybrid mode: TIER_0 executes
# ---------------------------------------------------------------------------

def test_hybrid_executes_tier_0_proposals(db, merchant_a):
    """In hybrid mode, TIER_0 proposals from Claude are executed."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        # Create an alert that resolve_alert can target
        alert = OpsAlert(
            severity="info", source="test", alert_type="test_hybrid",
            summary="hybrid test", created_at=_now(),
        )
        db.add(alert)
        db.flush()
        alert_id = alert.id

        mock_result = LLMDecisionResult(
            assessment="Resolve stale alert",
            model_used="test-model",
            raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="resolve_alert", target=str(alert_id), reason="Stale test alert", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            result = run_orchestrator_cycle(db)

        # Check that the alert was actually resolved
        row = db.execute(text("SELECT resolved FROM ops_alerts WHERE id = :id"), {"id": alert_id}).fetchone()
        assert row[0] is True

        # Check audit log has executed status
        audit = db.execute(text(
            "SELECT status, action_type FROM audit_log "
            "WHERE actor_name = 'orchestrator_claude' AND target_id = :tid "
            "ORDER BY id DESC LIMIT 1"
        ), {"tid": str(alert_id)}).fetchone()
        assert audit is not None
        assert audit[0] == "executed"
        assert "exec" in audit[1]

    finally:
        orch.ORCHESTRATOR_MODE = original


# ---------------------------------------------------------------------------
# Hybrid mode: higher tiers blocked
# ---------------------------------------------------------------------------

def test_hybrid_blocks_higher_tier_proposals(db, merchant_a):
    """In hybrid mode, non-TIER_0 proposals are logged but NOT executed."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original_mode = orch.ORCHESTRATOR_MODE
    original_registry = dict(ACTION_REGISTRY)

    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        # Temporarily add a TIER_1 action
        ACTION_REGISTRY["restart_worker"] = (
            lambda db, target: "restarted",
            "Restart a worker process",
            TIER_1,
        )

        mock_result = LLMDecisionResult(
            assessment="Worker needs restart",
            model_used="test-model",
            raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="restart_worker", target="intelligence_worker", reason="Stuck", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            result = run_orchestrator_cycle(db)

        # Check audit log has blocked_tier status
        audit = db.execute(text(
            "SELECT status FROM audit_log "
            "WHERE actor_name = 'orchestrator_claude' AND target_id = 'intelligence_worker' "
            "ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert audit is not None
        assert audit[0] == "awaiting_approval"

    finally:
        orch.ORCHESTRATOR_MODE = original_mode
        ACTION_REGISTRY.clear()
        ACTION_REGISTRY.update(original_registry)


# ---------------------------------------------------------------------------
# Proposal mode unchanged
# ---------------------------------------------------------------------------

def test_proposal_mode_never_executes(db, merchant_a):
    """Proposal mode logs proposals but never calls action functions."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "proposal"

        alert = OpsAlert(
            severity="info", source="test", alert_type="test_proposal",
            summary="proposal test", created_at=_now(),
        )
        db.add(alert)
        db.flush()

        mock_result = LLMDecisionResult(
            assessment="test", model_used="test",
            raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="resolve_alert", target=str(alert.id), reason="test", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            result = run_orchestrator_cycle(db)

        # Alert should NOT be resolved (proposal mode doesn't execute)
        row = db.execute(text("SELECT resolved FROM ops_alerts WHERE id = :id"), {"id": alert.id}).fetchone()
        assert row[0] is False

    finally:
        orch.ORCHESTRATOR_MODE = original


# ---------------------------------------------------------------------------
# Deterministic mode unchanged
# ---------------------------------------------------------------------------

def test_deterministic_mode_unaffected_by_tiers(db, merchant_a):
    """Deterministic mode still works — no LLM, no tier filtering."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "deterministic"

        # Old info alert → should be auto-resolved by deterministic rules
        alert = OpsAlert(
            severity="info", source="test", alert_type="webhook_repaired",
            summary="old alert", created_at=_now() - timedelta(hours=5),
        )
        db.add(alert)
        db.flush()

        result = run_orchestrator_cycle(db)
        assert result.actions_executed >= 1

    finally:
        orch.ORCHESTRATOR_MODE = original


# ---------------------------------------------------------------------------
# Audit logging structure
# ---------------------------------------------------------------------------

def test_hybrid_audit_log_contains_tier(db, merchant_a):
    """Audit log entries from hybrid mode include tier information."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        alert = OpsAlert(
            severity="info", source="test", alert_type="test_audit",
            summary="audit tier test", created_at=_now(),
        )
        db.add(alert)
        db.flush()

        mock_result = LLMDecisionResult(
            assessment="test", model_used="test", raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="resolve_alert", target=str(alert.id), reason="test", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            run_orchestrator_cycle(db)

        audit = db.execute(text(
            "SELECT after_state FROM audit_log "
            "WHERE actor_name = 'orchestrator_claude' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert audit is not None
        import json
        state = json.loads(audit[0])
        assert "tier" in state
        assert state["tier"] == TIER_0
        assert state["executed"] is True

    finally:
        orch.ORCHESTRATOR_MODE = original
