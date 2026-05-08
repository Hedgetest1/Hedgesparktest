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


def test_brain_vero_eval_unknown_metric_returns_evaluation_failed(db):
    """Defensive: when _measure() encounters a metric it doesn't know,
    it must return `evaluation_failed` (visible to audit), NOT `neutral`
    (silent masking).

    Bug 2026-05-08: Rule 4 of _decide() set `cvr_delta_7d` but _measure
    had no implementation → fell through to `return "neutral"` → every
    proactive_nudge_compose decision was stamped neutral regardless of
    whether the nudge worked. The LEARN limb couldn't distinguish working
    rules from broken ones."""
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import _measure
    from app.models.brain_decision import BrainDecision

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decision = BrainDecision(
            shop_domain="_brain_unknown_metric_test_.myshopify.com",
            decision_at=now - timedelta(hours=48),
            sense_snapshot={},
            synthesis="test",
            action_kind="test_unknown",
            action_payload={},
            rationale="test",
            limb_dispatched=None,
            limb_response={},
            expected_outcome_metric="totally_made_up_metric_xyz",
            outcome_window_hours=24,
        )
        db.add(decision)
        db.flush()

        result = _measure(db, decision)
        assert result == "evaluation_failed", (
            f"unknown metric must return evaluation_failed (visible), "
            f"NOT silent fallthrough. Got: {result!r}"
        )
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev


def test_brain_vero_eval_cvr_delta_7d_evaluation_failed_when_no_baseline(db):
    """Rule 4 sets `cvr_delta_7d` with `baseline_value=None`. _measure
    must return `evaluation_failed` honestly when baseline is missing
    (cannot compute delta without baseline). Closes the silent-neutral
    bug: previously cvr_delta_7d fell through to default `neutral`."""
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import _measure
    from app.models.brain_decision import BrainDecision

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decision = BrainDecision(
            shop_domain="_brain_cvr_test_.myshopify.com",
            decision_at=now - timedelta(hours=48),
            sense_snapshot={},
            synthesis="test",
            action_kind="proactive_nudge_compose",
            action_payload={},
            rationale="test",
            limb_dispatched=None,
            limb_response={},
            expected_outcome_metric="cvr_delta_7d",
            outcome_window_hours=168,
            baseline_value=None,  # Rule 4 sets this to None today
        )
        db.add(decision)
        db.flush()

        result = _measure(db, decision)
        assert result == "evaluation_failed", (
            f"cvr_delta_7d with no baseline must return evaluation_failed "
            f"(visible), NOT silent neutral. Got: {result!r}"
        )
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev


def test_brain_vero_eval_metric_events_24h_resumed_runs(db):
    """Regression test: previous outcome eval test used `cooldown_pending`
    which short-circuits before any DB query. The `events_24h_resumed`
    metric path queries `events.timestamp >= :decision_at` — and was
    BROKEN: events.timestamp is BigInteger (epoch ms), not a Postgres
    timestamp. Comparison `bigint >= timestamp` aborts the transaction.

    Discovered 2026-05-08 by running evaluate_pending_outcomes against
    LIVE brain_decisions. This test exercises the code path that was
    actually firing in prod, not just the cooldown shortcut."""
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import evaluate_pending_outcomes
    from app.models.brain_decision import BrainDecision

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Forge a decision 48h old, 24h window, REAL DB-querying metric.
        forged = BrainDecision(
            shop_domain="_brain_eval_query_test_.myshopify.com",
            decision_at=now - timedelta(hours=48),
            sense_snapshot={"rar_eur": 0.0, "churn_score": 0.5, "orders_24h": 0, "events_24h": 0},
            synthesis="forged for events_24h_resumed eval test",
            action_kind="re_engagement_check",
            action_payload={},
            rationale="test",
            limb_dispatched=None,
            limb_response={},
            expected_outcome_metric="events_24h_resumed",
            outcome_window_hours=24,
            baseline_value=0.0,
        )
        db.add(forged)
        db.flush()

        result = evaluate_pending_outcomes(db, max_evaluate=10)
        assert result.get("evaluated", 0) >= 1, (
            f"events_24h_resumed metric must evaluate without DB error, got {result}"
        )
        db.refresh(forged)
        # `evaluation_failed` is what we used to get on the bug. With
        # the fix, we must get an honest verdict (effective/ineffective/
        # neutral), never `evaluation_failed`.
        assert forged.outcome_status != "evaluation_failed", (
            f"events_24h_resumed query must succeed, got outcome_status="
            f"{forged.outcome_status} — likely the BigInteger cast bug "
            f"resurfaced"
        )
        assert forged.outcome_status in ("effective", "ineffective", "neutral"), (
            f"unexpected outcome_status: {forged.outcome_status}"
        )
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev
