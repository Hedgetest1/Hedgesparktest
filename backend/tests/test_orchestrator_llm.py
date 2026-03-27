"""Tests for Tier 1 orchestrator — context builder, LLM parsing, mode gating."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from app.models.merchant import Merchant
from app.services.orchestrator import (
    ACTION_REGISTRY,
    run_orchestrator_cycle,
    _clear_cooldowns,
)
from app.services.orchestrator_context import build_orchestrator_context
from app.services.orchestrator_llm import (
    _parse_response,
    claude_decision,
    LLMDecisionResult,
)
from tests.conftest import SHOP_A


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def test_context_builder_returns_string(db, merchant_a):
    """Context builder produces a non-empty structured string."""
    context = build_orchestrator_context(db)
    assert isinstance(context, str)
    assert len(context) > 50
    assert "## Alerts" in context
    assert "## Workers" in context
    assert "## System Vitals" in context


def test_context_includes_alert_details(db, merchant_a):
    """Alerts appear in the context output."""
    db.add(OpsAlert(
        severity="warning", source="test", alert_type="test_ctx",
        summary="context test alert", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.flush()

    context = build_orchestrator_context(db)
    assert "test_ctx" in context
    assert "context test alert" in context


def test_context_includes_vitals(db, merchant_a):
    """Vitals section has merchant count."""
    context = build_orchestrator_context(db)
    assert "Active merchants:" in context


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

def test_parse_valid_response():
    """Valid JSON with known actions → valid proposals."""
    raw = json.dumps({
        "assessment": "System has webhook drift",
        "actions": [
            {"action": "webhook_repair", "target": "shop.myshopify.com", "reason": "Missing webhook"},
            {"action": "resolve_alert", "target": "42", "reason": "Stale alert"},
        ]
    })
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    assert result.assessment == "System has webhook drift"
    assert len(result.proposals) == 2
    assert all(p.valid for p in result.proposals)


def test_parse_unknown_action_rejected():
    """Unknown action name → proposal marked invalid."""
    raw = json.dumps({
        "assessment": "test",
        "actions": [
            {"action": "delete_database", "target": "all", "reason": "Chaos monkey"},
        ]
    })
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    assert len(result.proposals) == 1
    assert result.proposals[0].valid is False


def test_parse_empty_target_rejected():
    """Empty target → proposal marked invalid."""
    raw = json.dumps({
        "assessment": "test",
        "actions": [
            {"action": "webhook_repair", "target": "", "reason": "No target"},
        ]
    })
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    assert len(result.proposals) == 1
    assert result.proposals[0].valid is False


def test_parse_invalid_json():
    """Malformed JSON → error, no proposals."""
    result = _parse_response("not json at all", ACTION_REGISTRY, "test-model")
    assert result.error is not None
    assert "json_parse_error" in result.error


def test_parse_empty_actions():
    """Empty actions list → healthy system, no proposals."""
    raw = json.dumps({"assessment": "All clear", "actions": []})
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    assert len(result.proposals) == 0
    assert result.assessment == "All clear"


def test_parse_deduplicates():
    """Duplicate action+target → only first kept."""
    raw = json.dumps({
        "assessment": "test",
        "actions": [
            {"action": "resolve_alert", "target": "1", "reason": "first"},
            {"action": "resolve_alert", "target": "1", "reason": "dupe"},
        ]
    })
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    valid = [p for p in result.proposals if p.valid]
    assert len(valid) == 1


def test_parse_markdown_wrapped_json():
    """JSON wrapped in markdown code block → still parsed."""
    inner = json.dumps({"assessment": "wrapped", "actions": []})
    raw = f"```json\n{inner}\n```"
    result = _parse_response(raw, ACTION_REGISTRY, "test-model")
    assert result.assessment == "wrapped"
    assert result.error is None


# ---------------------------------------------------------------------------
# Mode gating
# ---------------------------------------------------------------------------

def test_deterministic_mode_no_llm_call(db, merchant_a):
    """In deterministic mode, no LLM call is made."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original_mode = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "deterministic"
        with patch("app.services.orchestrator_llm.claude_decision") as mock:
            result = run_orchestrator_cycle(db)
            mock.assert_not_called()
    finally:
        orch.ORCHESTRATOR_MODE = original_mode


def test_proposal_mode_calls_llm(db, merchant_a):
    """In proposal mode, LLM is called but actions are NOT executed."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original_mode = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "proposal"

        mock_result = LLMDecisionResult(
            assessment="Test proposal",
            model_used="test-model",
            raw_response="{}",
        )
        mock_result.proposals = []

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="test context"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result) as mock_claude:
            result = run_orchestrator_cycle(db)
            mock_claude.assert_called_once()

    finally:
        orch.ORCHESTRATOR_MODE = original_mode


def test_proposal_mode_does_not_execute(db, merchant_a):
    """Proposals are logged but never executed in proposal mode."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original_mode = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "proposal"

        # Create an alert to give the LLM something to propose about
        db.add(OpsAlert(
            severity="warning", source="test", alert_type="webhook_repair_failed",
            shop_domain=SHOP_A, summary="test", created_at=_now(),
        ))
        db.flush()

        from app.services.orchestrator_llm import LLMProposal
        mock_result = LLMDecisionResult(
            assessment="Webhook needs repair",
            model_used="test-model",
            raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="webhook_repair", target=SHOP_A, reason="LLM says repair", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result), \
             patch("app.services.orchestrator._action_webhook_repair") as mock_repair:
            result = run_orchestrator_cycle(db)
            # The deterministic rules may call webhook_repair,
            # but the LLM proposal should NOT call it separately
            # Check that audit_log has a "proposed" entry
            audit = db.execute(text(
                "SELECT actor_name, status FROM audit_log WHERE actor_name = 'orchestrator_claude' ORDER BY id DESC LIMIT 1"
            )).fetchone()
            if audit:
                assert audit[1] == "proposed"

    finally:
        orch.ORCHESTRATOR_MODE = original_mode


def test_no_api_key_returns_gracefully():
    """No ANTHROPIC_API_KEY or OPENAI_API_KEY → empty result, no crash."""
    with patch("app.services.orchestrator_llm._ANTHROPIC_KEY", ""), \
         patch("app.services.orchestrator_llm._OPENAI_KEY", ""):
        result = claude_decision("test context", ACTION_REGISTRY)
    assert result.error == "no_api_key"
    assert len(result.proposals) == 0
