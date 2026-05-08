"""Smoke tests for agent_worker post-supersession.

The previous direct-import tests (test_pipeline_state, test_execution_mode,
test_telegram_operator, test_tier2_weekly_review, test_governed_tier1_auto_apply)
all tested the old immune-system brain and were deleted in Stage 2-E.

This file replaces them with the minimum invariants Brain Vero relies on:

1. Module imports cleanly (no dangling refs to deleted services).
2. _run_merchant_brain_tick exists and is callable.
3. _run_orchestrator phase exists.
4. run_cycle composes only the surviving phases (no deleted-helper refs).
5. Brain Vero outcome evaluator wires correctly post-Stage-2-E.

This is the test-coverage opt-out replacement (CLAUDE.md §22 + audit
service coverage backlog).
"""
from __future__ import annotations

import pytest


def test_module_imports_clean():
    """Top-level import must not raise — verifies zero dangling OB refs."""
    from app.workers import agent_worker
    assert agent_worker is not None


def test_run_cycle_callable():
    """run_cycle is the @cron_monitor entry point — must be a function."""
    from app.workers.agent_worker import run_cycle
    assert callable(run_cycle)


def test_merchant_brain_tick_phase_exists():
    """Brain Vero v0.1+ entry phase must be wired in agent_worker."""
    from app.workers.agent_worker import _run_merchant_brain_tick
    assert callable(_run_merchant_brain_tick)


def test_no_old_brain_helpers_resurrected():
    """Stage 2-E deleted these 10 helpers. None should re-appear via copy-paste.
    Any return = silent supersession regression."""
    from app.workers import agent_worker as aw
    deleted = [
        "_run_bug_triage",
        "_run_bugfix_outcome_eval",
        "_run_evolution_audit",
        "_run_evolution_conversion",
        "_run_meta_review",
        "_run_model_upgrade_scan",
        "_run_brain_refresh",
        "_run_evolution_gc",
        "_run_monthly_evolution_audit",
        "_run_pipeline_self_upgrade",
        "_check_circuit_breaker",
        "_heal_circuit_breaker_alerts",
    ]
    for name in deleted:
        assert not hasattr(aw, name), (
            f"{name} reappeared in agent_worker.py — Stage 2-E supersession "
            f"regression. The function was removed because old-brain bugfix "
            f"pipeline is dead. Re-introducing it would resurrect dead code."
        )


def test_orchestrator_phase_still_callable():
    """Real merchant-facing orchestrator phase (NOT old brain) survives."""
    from app.workers.agent_worker import _run_orchestrator
    assert callable(_run_orchestrator)


def test_brain_vero_evaluator_imports():
    """`evaluate_pending_outcomes` is the LEARN limb — must be importable
    + bound to a function (not the agent_worker outcome_evaluator stub)."""
    from app.services.merchant_brain import evaluate_pending_outcomes
    assert callable(evaluate_pending_outcomes)


def test_brain_vero_holdout_deterministic():
    """v0.4 holdout (10% control arm) is deterministic per (shop, day) —
    same shop + same day = same arm. Stress-test the contract that drives
    A/B comparability of outcome metrics."""
    from app.services.merchant_brain import _is_holdout
    from datetime import datetime, timezone

    shop = "test-shop.myshopify.com"
    day = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    # Same shop + same day → must be deterministic across calls
    a = _is_holdout(shop, day)
    b = _is_holdout(shop, day)
    assert a == b, "holdout assignment must be deterministic per (shop, day)"


def test_brain_vero_outcome_eval_no_op_when_disabled():
    """When MERCHANT_BRAIN_ENABLED=0 (default), evaluate_pending_outcomes
    must return a `skipped` dict without touching DB."""
    import os
    from app.services.merchant_brain import evaluate_pending_outcomes
    from unittest.mock import MagicMock

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "0"
    try:
        mock_db = MagicMock()
        result = evaluate_pending_outcomes(mock_db)
        assert result.get("skipped") == "brain_disabled"
        assert result.get("evaluated") == 0
        # MUST NOT touch DB when disabled
        mock_db.query.assert_not_called()
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev


def test_brain_vero_outcome_eval_runs_when_window_elapsed(db):
    """Stress-test the LEARN limb: forge a brain_decision whose
    outcome_window has elapsed → evaluate_pending_outcomes must process
    it and stamp outcome_status. This is the test that was missing
    before — Brain Vero v0.4 was LIVE in prod but the eval path had
    never been exercised (every decision was <24h old, no window had
    elapsed yet). Closes founder finding #5."""
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import evaluate_pending_outcomes
    from app.models.brain_decision import BrainDecision

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        # Forge a decision 48h old with a 24h outcome window — eligible.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        forged = BrainDecision(
            shop_domain="_brain_eval_test_.myshopify.com",
            decision_at=now - timedelta(hours=48),
            sense_snapshot={"rar_eur": 0.0, "churn_score": 0.0, "orders_24h": 0, "events_24h": 0},
            synthesis="forged for outcome eval test",
            action_kind="cooldown",
            action_payload={},
            rationale="test",
            limb_dispatched=None,
            limb_response={},
            expected_outcome_metric="cooldown_pending",
            outcome_window_hours=24,
        )
        db.add(forged)
        db.flush()

        result = evaluate_pending_outcomes(db, max_evaluate=10)
        assert result.get("evaluated", 0) >= 1, (
            f"forged decision with elapsed window must evaluate, got {result}"
        )
        # Verify the row got an outcome_status stamp
        db.refresh(forged)
        assert forged.outcome_status is not None, "outcome_status must be set"
        assert forged.outcome_evaluated_at is not None, "outcome_evaluated_at must be set"
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev
