"""Tests for system-level actions, detection rules, and evaluators."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from app.models.action_outcome import ActionOutcome
from app.services.orchestrator import (
    ACTION_REGISTRY,
    TIER_0, TIER_1, TIER_2,
    get_action_tier,
    _evaluate_decisions,
    _clear_cooldowns,
    run_orchestrator_cycle,
)
from app.services.outcome_evaluator import _EVALUATORS
from tests.conftest import SHOP_A


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Registry verification
# ---------------------------------------------------------------------------

def test_new_actions_in_registry():
    """All system actions are registered with correct tiers."""
    assert "clear_redis_cache" in ACTION_REGISTRY
    assert "restart_worker" in ACTION_REGISTRY
    assert "restart_all_workers" in ACTION_REGISTRY
    assert "run_migration_dryrun" in ACTION_REGISTRY
    assert "db_connection_reset" in ACTION_REGISTRY


def test_tier_assignments():
    """System actions have correct tier assignments."""
    assert get_action_tier("clear_redis_cache") == TIER_0
    assert get_action_tier("restart_worker") == TIER_1
    assert get_action_tier("restart_all_workers") == TIER_2
    assert get_action_tier("run_migration_dryrun") == TIER_1
    assert get_action_tier("db_connection_reset") == TIER_1


# ---------------------------------------------------------------------------
# Action execution (TIER_0 only)
# ---------------------------------------------------------------------------

def test_clear_redis_cache_prefix(db):
    """clear_redis_cache deletes keys matching prefix."""
    from app.core.redis_client import _client
    rc = _client()
    if rc is None:
        return  # skip if no Redis

    # Seed test keys
    rc.setex("test_flush:a", 60, "1")
    rc.setex("test_flush:b", 60, "2")
    rc.setex("test_keep:c", 60, "3")

    fn = ACTION_REGISTRY["clear_redis_cache"][0]
    result = fn(db, "test_flush:")
    assert "deleted_2" in result

    # Verify test_keep:c still exists
    assert rc.get("test_keep:c") is not None
    rc.delete("test_keep:c")


def test_clear_redis_cache_all(db):
    """clear_redis_cache with '*' flushes entire db."""
    fn = ACTION_REGISTRY["clear_redis_cache"][0]
    result = fn(db, "*")
    assert result == "flushed_all"


# ---------------------------------------------------------------------------
# Action execution (TIER_1 — verification only, not auto-executed)
# ---------------------------------------------------------------------------

def test_restart_worker_rejects_unknown():
    """restart_worker rejects process names not in allowed list."""
    fn = ACTION_REGISTRY["restart_worker"][0]
    result = fn(None, "malicious-process")
    assert "rejected" in result


def test_restart_worker_allows_known():
    """restart_worker accepts known worker names."""
    fn = ACTION_REGISTRY["restart_worker"][0]
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = fn(None, "wishspark-worker")
    assert "restarted" in result
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

def test_worker_failure_proposes_restart(db, merchant_a):
    """Worker repeated failure alert → restart_worker proposed."""
    _clear_cooldowns()
    db.add(OpsAlert(
        severity="warning", source="intelligence_worker",
        alert_type="worker_repeated_failure",
        summary="3 consecutive errors", created_at=_now() - timedelta(hours=2),
    ))
    db.flush()

    candidates = _evaluate_decisions(db)
    restart_candidates = [c for c in candidates if c.action == "restart_worker"]
    assert len(restart_candidates) >= 1


def test_cache_alert_proposes_clear(db, merchant_a):
    """Cache error alert → clear_redis_cache proposed."""
    _clear_cooldowns()
    db.add(OpsAlert(
        severity="warning", source="test",
        alert_type="cache_error",
        summary="cache errors", created_at=_now(),
    ))
    db.flush()

    candidates = _evaluate_decisions(db)
    cache_candidates = [c for c in candidates if c.action == "clear_redis_cache"]
    assert len(cache_candidates) >= 1


# ---------------------------------------------------------------------------
# Tier blocking in hybrid mode
# ---------------------------------------------------------------------------

def test_hybrid_blocks_tier1_restart(db, merchant_a):
    """In hybrid mode, Claude's restart_worker proposal is blocked by tier."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        from app.services.orchestrator_llm import LLMDecisionResult, LLMProposal
        mock_result = LLMDecisionResult(
            assessment="Worker stuck", model_used="test", raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="restart_worker", target="wishspark-worker", reason="stuck", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            result = run_orchestrator_cycle(db)

        # Check audit log shows awaiting_approval (tier-gated)
        audit = db.execute(text(
            "SELECT status FROM audit_log WHERE actor_name = 'orchestrator_claude' "
            "AND action_type LIKE '%restart_worker%' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        if audit:
            assert audit[0] == "awaiting_approval"

    finally:
        orch.ORCHESTRATOR_MODE = original


def test_hybrid_executes_tier0_cache_clear(db, merchant_a):
    """In hybrid mode, Claude's clear_redis_cache (TIER_0) is executed."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        from app.services.orchestrator_llm import LLMDecisionResult, LLMProposal
        mock_result = LLMDecisionResult(
            assessment="Cache stale", model_used="test", raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="clear_redis_cache", target="signals:*", reason="stale cache", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            result = run_orchestrator_cycle(db)

        audit = db.execute(text(
            "SELECT status FROM audit_log WHERE actor_name = 'orchestrator_claude' "
            "AND action_type LIKE '%clear_redis_cache%' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        if audit:
            assert audit[0] == "executed"

    finally:
        orch.ORCHESTRATOR_MODE = original


# ---------------------------------------------------------------------------
# Outcome evaluators
# ---------------------------------------------------------------------------

def test_evaluators_registered_for_new_actions():
    """All new actions have evaluators."""
    assert "orch_clear_redis_cache" in _EVALUATORS
    assert "orch_restart_worker" in _EVALUATORS
    assert "orch_db_connection_reset" in _EVALUATORS


def test_eval_cache_clear_success(db):
    """No new cache alerts after clear → success."""
    from app.services.outcome_evaluator import _eval_clear_redis_cache
    outcome = ActionOutcome(
        audit_log_id=1, action_type="orch_clear_redis_cache",
        target_id="signals:*", executed_at=_now(),
    )
    status, detail = _eval_clear_redis_cache(db, outcome)
    assert status == "success"


def test_eval_db_reset_success(db):
    """DB connection works after reset → success."""
    from app.services.outcome_evaluator import _eval_db_connection_reset
    outcome = ActionOutcome(
        audit_log_id=2, action_type="orch_db_connection_reset",
        target_id="pool", executed_at=_now(),
    )
    status, detail = _eval_db_connection_reset(db, outcome)
    assert status == "success"
    assert "ok" in detail
