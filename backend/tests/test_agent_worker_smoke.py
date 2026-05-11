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


def test_brain_vero_outcome_stamp_writes_audit_log(db):
    """Sprint 1 #5 — outcome ledger immutable.

    Every outcome stamp in `evaluate_pending_outcomes` MUST write a
    hash-chained audit_log row with action_type=brain_decision_outcome_
    stamped. The audit row is forensic backbone for the holdout
    p<0.05 claim. Tamper-evident — the chain breaks if a row is
    altered/removed.

    Failure mode prevented: outcome_status field could be silently
    rewritten in DB without trace; audit chain makes that detectable.
    """
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import evaluate_pending_outcomes
    from app.models.brain_decision import BrainDecision
    from app.models.audit_log import AuditLog

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        forged = BrainDecision(
            shop_domain="_audit_ledger_test_.myshopify.com",
            decision_at=now - timedelta(hours=48),
            sense_snapshot={"rar_eur": 0.0, "churn_score": 0.0, "orders_24h": 0, "events_24h": 0},
            synthesis="forged for audit-ledger test",
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

        # Snapshot pre-existing audit rows for this synthetic shop
        # (should be 0 — synthetic shop_domain unused).
        pre_count = db.query(AuditLog).filter(
            AuditLog.shop_domain == "_audit_ledger_test_.myshopify.com",
            AuditLog.action_type == "brain_decision_outcome_stamped",
        ).count()

        result = evaluate_pending_outcomes(db, max_evaluate=10)
        assert result.get("evaluated", 0) >= 1, f"got {result}"

        post_count = db.query(AuditLog).filter(
            AuditLog.shop_domain == "_audit_ledger_test_.myshopify.com",
            AuditLog.action_type == "brain_decision_outcome_stamped",
        ).count()
        assert post_count == pre_count + 1, (
            f"audit_log must record outcome stamp: pre={pre_count} post={post_count}"
        )

        # Verify chain integrity on the new row
        new_row = db.query(AuditLog).filter(
            AuditLog.shop_domain == "_audit_ledger_test_.myshopify.com",
            AuditLog.action_type == "brain_decision_outcome_stamped",
        ).order_by(AuditLog.id.desc()).first()
        assert new_row is not None
        assert new_row.actor_type == "worker"
        assert new_row.actor_name == "merchant_brain.evaluate_pending_outcomes"
        assert new_row.target_type == "brain_decision"
        assert new_row.target_id == str(forged.id)
        assert new_row.approval_mode == "autonomous"
        # Hash chain metadata must be present (column is Text JSON)
        import json as _json
        meta = _json.loads(new_row.metadata_json) if new_row.metadata_json else {}
        chain = meta.get("_chain")
        assert chain is not None, "audit row must carry _chain metadata"
        assert chain.get("self") and chain.get("prev") and chain.get("digest"), (
            f"chain must have prev/self/digest, got {chain}"
        )
        # The after_state (Text JSON) captures the outcome
        after = _json.loads(new_row.after_state) if new_row.after_state else {}
        assert after.get("outcome_status") == forged.outcome_status
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev


def test_brain_vero_outcome_eval_triggers_closed_loop_sip_retrain(db):
    """Sprint 1 #6 — closed-loop trigger.

    Every outcome_status stamp in evaluate_pending_outcomes triggers
    an immediate compute_sip + upsert_sip for the affected shop.
    Dedup per shop: 3 decisions on 1 shop = 1 retrain, NOT 3.

    The fact we verify: the result dict reports `shops_retrained` >= 1
    and the function does NOT raise even when compute_sip falls back
    (synthetic shop with no events → compute_sip returns None → upsert
    skipped, no error).
    """
    import os
    from datetime import datetime, timedelta, timezone
    from app.services.merchant_brain import evaluate_pending_outcomes
    from app.models.brain_decision import BrainDecision

    prev = os.environ.get("MERCHANT_BRAIN_ENABLED")
    os.environ["MERCHANT_BRAIN_ENABLED"] = "1"
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 3 decisions, same shop — must dedup to 1 retrain attempt
        for i in range(3):
            forged = BrainDecision(
                shop_domain="_closedloop_test_.myshopify.com",
                decision_at=now - timedelta(hours=48 + i),
                sense_snapshot={"rar_eur": 0.0, "churn_score": 0.0, "orders_24h": 0, "events_24h": 0},
                synthesis=f"forged closed-loop test {i}",
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
        assert result.get("evaluated", 0) >= 3, f"all 3 must evaluate: {result}"
        assert result.get("shops_retrained", 0) == 1, (
            f"3 decisions same shop must dedup to 1 retrain attempt, got {result}"
        )
    finally:
        if prev is None:
            os.environ.pop("MERCHANT_BRAIN_ENABLED", None)
        else:
            os.environ["MERCHANT_BRAIN_ENABLED"] = prev


def test_autonomy_level_threshold_ladder():
    """Sprint 1 #2 — autonomy_level promotion 0→5 + Wilson-IC95% derived
    decisions-evaluated floors (Senior+++ close 2026-05-11).

    Floors derived in module-level comment:
        L3 floor = 100 evaluated decisions  (Wilson HW ≤ 0.10 per dim)
        L4 floor = 200                       (HW ≤ 0.07)
        L5 floor = 400                       (HW ≤ 0.05, most aggressive)
        L2-medium floor = 50                 (softer, assisted)

    The ladder is documented in StoreIntelligenceProfile:
    0=observe, 1=suggest, 2=assisted, 3=semi-auto, 4=full-auto,
    5=aggressive. Promotion gated by confidence + trust_score +
    measured-decision volume (decisions_evaluated).
    """
    from app.services.sip_engine import _autonomy_level_from_trust as f

    # Low confidence — locked at 0 (observe-only) regardless of decisions
    assert f(0.99, "low", decisions_evaluated=10000) == 0
    assert f(0.50, "low") == 0

    # Medium confidence — capped at 2; L2 requires >=50 evaluated decisions
    assert f(0.95, "medium", decisions_evaluated=50) == 2
    assert f(0.85, "medium", decisions_evaluated=50) == 2
    assert f(0.85, "medium", decisions_evaluated=49) == 1  # demoted
    assert f(0.70, "medium") == 1
    assert f(0.50, "medium") == 0

    # High confidence — full ladder 0..5 with Wilson-derived floors
    assert f(0.96, "high", decisions_evaluated=400) == 5  # aggressive
    assert f(0.87, "high", decisions_evaluated=200) == 4  # full-auto
    assert f(0.76, "high", decisions_evaluated=100) == 3  # semi-auto
    assert f(0.66, "high") == 2  # assisted (no floor)
    assert f(0.51, "high") == 1  # suggest (no floor)
    assert f(0.40, "high") == 0  # observe

    # Decisions-evaluated floor enforcement at boundaries
    # Trust 0.96 + 399 → fails L5 (needs >=400), passes L4 (>=200, 0.96>=0.85)
    assert f(0.96, "high", decisions_evaluated=399) == 4
    # Trust 0.96 + 199 → fails L5+L4, passes L3 (>=100, 0.96>=0.75)
    assert f(0.96, "high", decisions_evaluated=199) == 3
    # Trust 0.96 + 99 → fails L5/L4/L3, lands L2 (trust>=0.65, no floor)
    assert f(0.96, "high", decisions_evaluated=99) == 2
    # Trust 0.87 + 199 → fails L4 (needs >=200), passes L3
    assert f(0.87, "high", decisions_evaluated=199) == 3
    # Trust 0.87 + 99 → fails L4+L3, lands L2
    assert f(0.87, "high", decisions_evaluated=99) == 2
    # Trust 0.76 + 99 → fails L3, lands L2
    assert f(0.76, "high", decisions_evaluated=99) == 2

    # Default decisions=0: hot-streak trust=0.99 high cannot exceed L2
    # (this is the bug the floor closes — a shop with 0 measured
    # outcomes can no longer hit level 5 just because confidence flags
    # high from event count)
    assert f(0.99, "high") == 2
    assert f(0.96, "high") == 2
    assert f(0.87, "high") == 2
    assert f(0.76, "high") == 2


def test_wilson_floors_match_documented_derivation():
    """Lock the Wilson-CI derivation to the actual constants — if any
    threshold drifts, the test fails so the doctrine doesn't desync
    from the code. Math: HW ≈ 0.98/√n at p=0.5 worst-case binomial."""
    import math
    from app.services.sip_engine import (
        _AUTONOMY_L3_DECISIONS_FLOOR,
        _AUTONOMY_L4_DECISIONS_FLOOR,
        _AUTONOMY_L5_DECISIONS_FLOOR,
        _AUTONOMY_L2_MEDIUM_DECISIONS_FLOOR,
    )

    # L3: HW ≤ 0.10 → n ≥ ceil((0.98/0.10)²) = 97 → rounded to 100
    assert _AUTONOMY_L3_DECISIONS_FLOOR >= math.ceil((0.98 / 0.10) ** 2)
    assert _AUTONOMY_L3_DECISIONS_FLOOR == 100

    # L4: HW ≤ 0.07 → n ≥ ceil((0.98/0.07)²) = 197 → rounded to 200
    assert _AUTONOMY_L4_DECISIONS_FLOOR >= math.ceil((0.98 / 0.07) ** 2)
    assert _AUTONOMY_L4_DECISIONS_FLOOR == 200

    # L5: HW ≤ 0.05 → n ≥ ceil((0.98/0.05)²) = 385 → rounded to 400
    assert _AUTONOMY_L5_DECISIONS_FLOOR >= math.ceil((0.98 / 0.05) ** 2)
    assert _AUTONOMY_L5_DECISIONS_FLOOR == 400

    # L2-medium softer floor at 50 decisions
    assert _AUTONOMY_L2_MEDIUM_DECISIONS_FLOOR == 50


def test_autonomy_level_monotonic_floor(db):
    """Sprint 1 #2 — monotonic floor: never demote based on a single
    low computation. If shop A is at autonomy=4 and the next compute
    cycle returns 3, the row keeps 4. This protects against transient
    holdout misses dragging trust temporarily down.
    """
    from sqlalchemy import text as _sql_text
    from app.services.sip_engine import upsert_sip

    shop = "_autonomy_floor_test_.myshopify.com"
    conn = db.connection()
    # Seed: simulate row at autonomy_level=4
    conn.execute(_sql_text(
        """
        INSERT INTO store_intelligence_profiles (
            shop_domain, profile_version, data_points_total, confidence_level,
            trust_score, autonomy_level, computed_at, updated_at
        ) VALUES (
            :s, 1, 5000, 'high', 0.87, 4, NOW(), NOW()
        )
        ON CONFLICT (shop_domain) DO UPDATE SET autonomy_level = 4, trust_score = 0.87
        """
    ), {"s": shop})
    db.flush()

    # Simulate next cycle: trust drops to 0.76 (would be autonomy=3 via ladder)
    sip = {
        "shop_domain": shop,
        "profile_version": 1,
        "baseline_cart_rate": None,
        "baseline_scroll_depth": None,
        "baseline_dwell_time": None,
        "baseline_return_rate": None,
        "baseline_views_per_product": None,
        "baseline_mobile_pct": None,
        "learned_thresholds": None,
        "traffic_source_quality": None,
        "price_sensitivity_bands": None,
        "nudge_type_scores": None,
        "best_nudge_by_signal": None,
        "peak_traffic_hours": None,
        "signal_frequency_30d": None,
        "data_points_total": 5100,
        "confidence_level": "high",
        "computed_at": __import__("datetime").datetime.now(),
        "trust_score": 0.76,
        "trust_profile": None,
    }
    upsert_sip(conn, sip)
    db.flush()

    # Verify: autonomy_level stays at 4 (monotonic floor), NOT 3
    row = conn.execute(_sql_text(
        "SELECT autonomy_level FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row is not None
    assert row[0] == 4, f"monotonic floor must hold autonomy=4 even with trust=0.76, got {row[0]}"


def test_profile_version_bumps_only_on_model_artifact_hash_change(db):
    """Senior+++ semantic 2026-05-11: `profile_version` increments ONLY
    when `model_artifact_hash` changes (real model-state change).
    Two upserts with identical learned state leave the version
    unchanged — the version becomes meaningful, not a cosmetic upsert
    counter. Replaces the prior "+1 on every upsert" semantic born
    earlier same day.
    """
    from sqlalchemy import text as _sql_text
    from app.services.sip_engine import upsert_sip

    shop = "_profile_version_test_.myshopify.com"
    conn = db.connection()

    def _sip_template(thresholds: dict | None = None) -> dict:
        return {
            "shop_domain": shop,
            "profile_version": 1,  # INSERT default; UPSERT uses CASE
            "baseline_cart_rate": 0.05,  # non-null → contributes to hash
            "baseline_scroll_depth": None,
            "baseline_dwell_time": None, "baseline_return_rate": None,
            "baseline_views_per_product": None, "baseline_mobile_pct": None,
            "learned_thresholds": thresholds, "traffic_source_quality": None,
            "price_sensitivity_bands": None, "nudge_type_scores": None,
            "best_nudge_by_signal": None, "peak_traffic_hours": None,
            "signal_frequency_30d": None,
            "data_points_total": 1000,
            "confidence_level": "low",
            "computed_at": __import__("datetime").datetime.now(),
            "trust_score": 0.5,
            "trust_profile": None,
        }

    # Cycle 1: INSERT → profile_version = 1
    upsert_sip(conn, _sip_template({"v": 1}))
    db.flush()
    row = conn.execute(_sql_text(
        "SELECT profile_version, model_artifact_hash "
        "FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row[0] == 1
    initial_hash = row[1]
    assert initial_hash and len(initial_hash) == 64  # sha256 hex

    # Cycle 2: SAME model state → hash unchanged → version stays at 1
    upsert_sip(conn, _sip_template({"v": 1}))
    db.flush()
    row = conn.execute(_sql_text(
        "SELECT profile_version, model_artifact_hash "
        "FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row[0] == 1, "version must NOT bump when model state unchanged"
    assert row[1] == initial_hash

    # Cycle 3: thresholds change → hash changes → version bumps to 2
    upsert_sip(conn, _sip_template({"v": 2}))
    db.flush()
    row = conn.execute(_sql_text(
        "SELECT profile_version, model_artifact_hash "
        "FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row[0] == 2, "version must bump when model state changes"
    assert row[1] != initial_hash

    # Cycle 4: another change → version bumps to 3
    upsert_sip(conn, _sip_template({"v": 3}))
    db.flush()
    row = conn.execute(_sql_text(
        "SELECT profile_version FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row[0] == 3

    # Cycle 5: revert to v=2 thresholds → hash differs from v=3 → bump to 4
    # (the version reflects "number of distinct model states observed",
    # NOT "shortest path through state space")
    upsert_sip(conn, _sip_template({"v": 2}))
    db.flush()
    row = conn.execute(_sql_text(
        "SELECT profile_version FROM store_intelligence_profiles WHERE shop_domain = :s"
    ), {"s": shop}).fetchone()
    assert row[0] == 4


def test_model_artifact_hash_deterministic():
    """sha256 of the same SIP state must produce the same hash —
    deterministic, no time-component in the hash input."""
    from app.services.sip_engine import _model_artifact_hash

    sip_a = {
        "learned_thresholds": {"x": 1, "y": 2},
        "baseline_cart_rate": 0.05,
        "nudge_type_scores": {"social_proof": 0.8},
        "trust_score": 0.7,  # excluded from hash
        "computed_at": __import__("datetime").datetime(2026, 5, 11),  # excluded
    }
    sip_b = {
        "learned_thresholds": {"y": 2, "x": 1},  # key order differs
        "baseline_cart_rate": 0.05,
        "nudge_type_scores": {"social_proof": 0.8},
        "trust_score": 0.99,  # different but excluded
        "computed_at": __import__("datetime").datetime(2026, 6, 1),  # different but excluded
    }
    # Same model state, different stats → same hash
    assert _model_artifact_hash(sip_a) == _model_artifact_hash(sip_b)

    sip_c = {
        "learned_thresholds": {"x": 999, "y": 2},  # changed
        "baseline_cart_rate": 0.05,
        "nudge_type_scores": {"social_proof": 0.8},
    }
    # Different model state → different hash
    assert _model_artifact_hash(sip_a) != _model_artifact_hash(sip_c)


def _insert_decision_with_status(db, shop: str, status: str | None) -> int:
    """Insert a brain_decisions row, return id. Helper for trigger tests."""
    from sqlalchemy import text as _sql_text
    db.execute(_sql_text("""
        INSERT INTO brain_decisions
            (decision_at, shop_domain, action_kind,
             expected_outcome_metric, outcome_window_hours,
             outcome_status,
             outcome_evaluated_at)
        VALUES
            (now(), :s, 'test_action', 'rars_delta_7d', 24,
             :status,
             CASE WHEN :status IS NOT NULL THEN now() ELSE NULL END)
    """), {"s": shop, "status": status})
    db.flush()
    return db.execute(_sql_text(
        "SELECT id FROM brain_decisions WHERE shop_domain = :s "
        "ORDER BY id DESC LIMIT 1"
    ), {"s": shop}).scalar()


def test_brain_decisions_immutability_blocks_set_to_different(db):
    """DB trigger raises when outcome_status changes set → different.
    Born 2026-05-11 Senior+++ close (audit #3)."""
    from sqlalchemy import text as _sql_text
    from sqlalchemy.exc import InternalError, IntegrityError

    decision_id = _insert_decision_with_status(
        db, "_immut_a_.myshopify.com", "effective"
    )

    raised = False
    nested = db.begin_nested()  # SAVEPOINT for surgical rollback
    try:
        db.execute(_sql_text(
            "UPDATE brain_decisions SET outcome_status = 'ineffective' "
            "WHERE id = :id"
        ), {"id": decision_id})
        db.flush()
        nested.commit()
    except (InternalError, IntegrityError) as e:
        raised = True
        assert "immutable" in str(e).lower() or "check_violation" in str(e).lower()
        nested.rollback()
    assert raised, "trigger MUST block set → different"


def test_brain_decisions_immutability_blocks_set_to_null(db):
    """DB trigger raises when outcome_status is reset set → NULL.
    The "unset" path is just as dangerous as "rewrite to different" —
    both break the forensic immutability claim."""
    from sqlalchemy import text as _sql_text
    from sqlalchemy.exc import InternalError, IntegrityError

    decision_id = _insert_decision_with_status(
        db, "_immut_b_.myshopify.com", "effective"
    )

    raised = False
    nested = db.begin_nested()
    try:
        db.execute(_sql_text(
            "UPDATE brain_decisions SET outcome_status = NULL "
            "WHERE id = :id"
        ), {"id": decision_id})
        db.flush()
        nested.commit()
    except (InternalError, IntegrityError) as e:
        raised = True
        nested.rollback()
    assert raised, "trigger MUST block set → NULL"


def test_brain_decisions_immutability_allows_null_to_set(db):
    """The legitimate path NULL → set must remain unblocked. This is
    the initial outcome stamp by the brain's _evaluate phase."""
    from sqlalchemy import text as _sql_text

    decision_id = _insert_decision_with_status(
        db, "_immut_c_.myshopify.com", None  # start NULL
    )
    # NULL → set is allowed
    db.execute(_sql_text(
        "UPDATE brain_decisions SET outcome_status = 'effective' "
        "WHERE id = :id"
    ), {"id": decision_id})
    db.flush()  # No raise

    # Verify written
    status = db.execute(_sql_text(
        "SELECT outcome_status FROM brain_decisions WHERE id = :id"
    ), {"id": decision_id}).scalar()
    assert status == "effective"


def test_brain_decisions_immutability_allows_idempotent_restamp(db):
    """Re-stamping the same value (e.g., evaluator re-runs on same row)
    is a no-op-shaped UPDATE and must be allowed. NEW == OLD → trigger
    falls through."""
    from sqlalchemy import text as _sql_text

    decision_id = _insert_decision_with_status(
        db, "_immut_d_.myshopify.com", "effective"
    )
    # Restamp same value → allowed (NEW == OLD)
    db.execute(_sql_text(
        "UPDATE brain_decisions SET outcome_status = 'effective' "
        "WHERE id = :id"
    ), {"id": decision_id})
    db.flush()  # No raise


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
