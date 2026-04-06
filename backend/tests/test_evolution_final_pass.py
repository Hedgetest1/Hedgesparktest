"""
Tests for the final-pass self-driving loop:

  1. Commit blast radius — multi-file rollback + TIER_2 blast-radius block
  2. Reinforcement loop — weights compute, priority_score multiplier applies
  3. Causal attribution via holdout — presence/absence, sample gates
  4. Failed-rollback escalation — ops_alert written + deduped
  5. Auto-extend — creates deeper variant, dedups, respects confidence gate
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.models.ops_alert import OpsAlert


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_proposal(
    db, *,
    reason="Improve cart conversion",
    target_file="app/services/orchestrator_llm.py",
    applied_commit_sha="deadbeef1234",
    tech_outcome="ineffective",
    business_outcome="declined",
    confidence_score=0.85,
    affected_files=None,
    linked_nudge_ids=None,
    business_measured_at=None,
) -> EvolutionProposal:
    p = EvolutionProposal(
        proposal_type="architecture",
        risk_level="LEVEL_2",
        reason=reason,
        expected_impact="impact",
        auto_applicable=False,
        status="accepted",
        audit_cycle="9999-M99",
        dedup_key=f"monthly_opus:9999-M99:{reason[:40]}",
        target_file=target_file,
        applied_at=_utcnow() - timedelta(days=40),
        applied_commit_sha=applied_commit_sha,
        outcome_status=tech_outcome,
        business_outcome=business_outcome,
        business_measured_at=business_measured_at or _utcnow(),
        confidence_score=confidence_score,
        affected_files=json.dumps(affected_files) if affected_files else None,
        linked_nudge_ids=json.dumps(linked_nudge_ids) if linked_nudge_ids else None,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# FIX 1 — Commit blast radius
# ---------------------------------------------------------------------------

def test_blast_radius_multi_file_rollback_includes_all_files(db):
    from app.services.evolution_decision_engine import propose_rollback
    with patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count", return_value=0,
    ):
        p = _make_proposal(db, affected_files=[
            "app/services/orchestrator_llm.py",
            "app/services/merge_intelligence.py",
            "app/core/llm_router.py",  # TIER_0/1, not TIER_2
        ])
        status, cid, reason = propose_rollback(db, p)
    assert status == "rollback_proposed"
    assert cid is not None
    cand = db.query(BugFixCandidate).filter(BugFixCandidate.id == cid).first()
    ctx = json.loads(cand.context_json)
    assert len(ctx["affected_files"]) == 3
    assert "app/services/merge_intelligence.py" in ctx["affected_files"]
    assert "reverse patch" in ctx["instruction"].lower() or "reverse" in ctx["instruction"].lower()
    assert "3 file(s)" in ctx["instruction"]


def test_blast_radius_blocks_when_any_file_is_tier_2(db):
    from app.services.evolution_decision_engine import propose_rollback
    # target_file is harmless, but the commit also touched .env → block
    p = _make_proposal(db, affected_files=[
        "app/services/orchestrator_llm.py",
        ".env",  # TIER_2
    ])
    status, cid, reason = propose_rollback(db, p)
    assert status == "rollback_blocked"
    assert cid is None
    assert "blast_radius_contains_tier2" in reason
    assert ".env" in reason


def test_blast_radius_target_file_fallback_when_no_affected_files(db):
    """If affected_files is empty AND git isn't available, fall back to target_file."""
    from app.services.evolution_decision_engine import propose_rollback
    with patch(
        "app.services.evolution_decision_engine.extract_commit_files", return_value=[],
    ), patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count", return_value=0,
    ):
        p = _make_proposal(
            db, target_file="app/services/orchestrator_llm.py", affected_files=None,
        )
        status, cid, reason = propose_rollback(db, p)
    assert status == "rollback_proposed"
    assert cid is not None


def test_blast_radius_blocks_tier_2_on_fallback(db):
    """Fallback to target_file must still enforce TIER_2."""
    from app.services.evolution_decision_engine import propose_rollback
    with patch(
        "app.services.evolution_decision_engine.extract_commit_files", return_value=[],
    ):
        p = _make_proposal(db, target_file=".env", affected_files=None)
        status, cid, reason = propose_rollback(db, p)
    assert status == "rollback_blocked"
    assert "blast_radius_contains_tier2" in reason


# ---------------------------------------------------------------------------
# FIX 2 — Reinforcement loop
# ---------------------------------------------------------------------------

def test_reinforcement_weights_winning_domain_boosts(db):
    from app.services.evolution_reinforcement import compute_reinforcement_weights
    # Seed: 6 BOTH wins in conversion, 1 NEITHER loss in conversion.
    # Reasons must contain a revenue keyword so classify_business_domain
    # routes them to 'conversion'.
    for i in range(6):
        _make_proposal(
            db, reason=f"Improve cart conversion nudge variant {i}",
            tech_outcome="effective", business_outcome="improved",
            confidence_score=0.8,
        )
    _make_proposal(
        db, reason="Improve cart conversion loser",
        tech_outcome="ineffective", business_outcome="declined",
        confidence_score=0.85,
    )
    weights = compute_reinforcement_weights(db, days=365)
    conv = weights["conversion"]
    assert conv["wins"] >= 6
    assert conv["losses"] >= 1
    assert conv["weight"] >= 0.80  # strong reinforcement


def test_reinforcement_weights_dampened_on_few_samples(db):
    from app.services.evolution_reinforcement import compute_reinforcement_weights
    _make_proposal(
        db, reason="Improve cart conversion solo winner",
        tech_outcome="effective", business_outcome="improved",
        confidence_score=0.8,
    )
    weights = compute_reinforcement_weights(db, days=365)
    # One-sample win is below _MIN_SAMPLES=5, so dampening must pull the
    # weight toward neutral 0.5 rather than swinging fully to 1.0.
    conv = weights["conversion"]
    assert conv["dampened"] is True
    assert 0.5 < conv["weight"] < 0.8


def test_priority_score_applies_reinforcement_multiplier():
    from app.services.evolution_business_outcomes import compute_priority_score

    class _P:
        reason = "Improve cart conversion"
        expected_impact = ""
        target_file = ""
        risk_level = "LEVEL_2"

    rates = {
        "conversion": {"improved": 5, "declined": 0, "stable": 0, "total": 5, "success_rate": 1.0},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
    }
    # Without reinforcement
    base = compute_priority_score(_P(), rates)
    # With strong conversion reinforcement (weight=1.0 → multiplier=1.5)
    weights = {
        "conversion": {"weight": 1.0, "wins": 8, "losses": 0,
                       "neutral": 0, "total": 8, "success_rate": 1.0, "dampened": False},
        "infra": {"weight": 0.5, "wins": 0, "losses": 0,
                  "neutral": 0, "total": 0, "success_rate": 0.0, "dampened": True},
    }
    boosted = compute_priority_score(_P(), rates, weights)
    assert boosted["score"] > base["score"]
    assert boosted["breakdown"]["reinforcement_multiplier"] == 1.5


def test_priority_score_penalizes_losing_domain():
    from app.services.evolution_business_outcomes import compute_priority_score

    class _P:
        reason = "Improve cart conversion"
        expected_impact = ""
        target_file = ""
        risk_level = "LEVEL_2"

    rates = {
        "conversion": {"improved": 5, "declined": 0, "stable": 0, "total": 5, "success_rate": 1.0},
    }
    # All-losses domain → weight=0.0 → multiplier=0.5
    losing = {
        "conversion": {"weight": 0.0, "wins": 0, "losses": 8,
                       "neutral": 0, "total": 8, "success_rate": 0.0, "dampened": False},
    }
    base = compute_priority_score(_P(), rates)
    penalized = compute_priority_score(_P(), rates, losing)
    assert penalized["score"] < base["score"]
    assert penalized["breakdown"]["reinforcement_multiplier"] == 0.5


# ---------------------------------------------------------------------------
# FIX 3 — Causal attribution (holdout integration)
# ---------------------------------------------------------------------------

def test_causal_attribution_returns_none_without_linked_nudges(db):
    """Proposals without linked_nudge_ids fall through to quasi-causal."""
    from app.services.evolution_causal_attribution import try_causal_measurement
    p = _make_proposal(db, linked_nudge_ids=None)
    result = try_causal_measurement(
        db, p,
        window_start=_utcnow() - timedelta(days=14),
        window_end=_utcnow(),
    )
    assert result is None


def test_causal_attribution_returns_none_on_insufficient_cohorts(db):
    """With linked nudges but no actual nudge_events, insufficient samples → None."""
    from app.services.evolution_causal_attribution import try_causal_measurement
    p = _make_proposal(db, linked_nudge_ids=[9999, 10000])
    result = try_causal_measurement(
        db, p,
        window_start=_utcnow() - timedelta(days=14),
        window_end=_utcnow(),
    )
    # No nudge_events exist for those synthetic IDs, so cohorts are empty
    assert result is None


def test_causal_attribution_with_seeded_cohorts(db):
    """Seed nudge_events + visitor_purchase_sessions to drive an end-to-end measurement."""
    from app.models.nudge_event import NudgeEvent
    from app.models.visitor_purchase_session import VisitorPurchaseSession
    from app.services.evolution_causal_attribution import try_causal_measurement

    shop = "causal-test.myshopify.com"
    nudge_id = 99_001
    now = _utcnow()
    # 600 exposed visitors, 50 orders from exposed → cvr=0.083
    # 600 control visitors, 30 orders from control → cvr=0.050
    # Causal delta +3.3pp (relative change +66%) → improved
    for i in range(600):
        db.add(NudgeEvent(
            shop_domain=shop, nudge_id=nudge_id, visitor_id=f"exp_{i}",
            product_url="/products/x", event_type="shown", created_at=now,
        ))
    for i in range(600):
        db.add(NudgeEvent(
            shop_domain=shop, nudge_id=nudge_id, visitor_id=f"ctl_{i}",
            product_url="/products/x", event_type="holdout_assigned", created_at=now,
        ))
    for i in range(50):
        db.add(VisitorPurchaseSession(
            shop_domain=shop, visitor_id=f"exp_{i}",
            shopify_order_id=f"order_exp_{i}", confirmed_at=now,
        ))
    for i in range(30):
        db.add(VisitorPurchaseSession(
            shop_domain=shop, visitor_id=f"ctl_{i}",
            shopify_order_id=f"order_ctl_{i}", confirmed_at=now,
        ))
    db.flush()

    p = _make_proposal(db, linked_nudge_ids=[nudge_id])
    result = try_causal_measurement(
        db, p,
        window_start=now - timedelta(minutes=5),
        window_end=now + timedelta(minutes=5),
    )
    assert result is not None
    outcome, evidence = result
    assert evidence["attribution_type"] == "causal"
    assert evidence["exposed"]["n_visitors"] == 600
    assert evidence["control"]["n_visitors"] == 600
    assert evidence["exposed"]["n_orders"] == 50
    assert evidence["control"]["n_orders"] == 30
    assert outcome == "improved"
    # Causal confidence should be high (big sample + big effect)
    assert evidence["confidence_score"] >= 0.5


# ---------------------------------------------------------------------------
# FIX 4 — Failed-rollback escalation
# ---------------------------------------------------------------------------

def test_failed_rollback_escalates_to_ops_alert(db):
    from app.services.evolution_decision_engine import escalate_failed_rollbacks
    p = _make_proposal(db, reason="bad proposal")
    cand = BugFixCandidate(
        source_type="auto_rollback",
        source_ref=f"evolution_{p.id}",
        title="[Auto-Rollback] reverse",
        status="apply_failed",
        failure_reason="patch did not apply cleanly: merge conflict",
    )
    db.add(cand)
    db.flush()

    summary = escalate_failed_rollbacks(db)
    assert summary["escalated"] == 1

    alerts = (
        db.query(OpsAlert)
        .filter(OpsAlert.alert_type == "rollback_failed")
        .all()
    )
    assert len(alerts) >= 1
    alert = alerts[-1]
    assert alert.severity == "critical"
    assert alert.source == "evolution_rollback_watchdog"
    assert str(p.id) in alert.summary
    # Detail must contain the failure context
    detail = json.loads(alert.detail) if alert.detail else {}
    assert detail.get("rollback_candidate_id") == cand.id
    assert detail.get("proposal_id") == p.id
    assert "merge conflict" in (detail.get("failure_reason") or "")


def test_failed_rollback_dedup_prevents_storm(db):
    """Running the watchdog twice must not create duplicate alerts."""
    from app.services.evolution_decision_engine import escalate_failed_rollbacks
    p = _make_proposal(db, reason="bad2")
    db.add(BugFixCandidate(
        source_type="auto_rollback",
        source_ref=f"evolution_{p.id}",
        title="[Auto-Rollback]",
        status="apply_failed",
    ))
    db.flush()
    escalate_failed_rollbacks(db)
    before = db.query(OpsAlert).filter(OpsAlert.alert_type == "rollback_failed").count()
    escalate_failed_rollbacks(db)
    after = db.query(OpsAlert).filter(OpsAlert.alert_type == "rollback_failed").count()
    assert after == before  # dedup via write_alert's 5min window


def test_failed_rollback_ignores_successful_candidates(db):
    """Applied rollbacks are NOT escalated."""
    from app.services.evolution_decision_engine import escalate_failed_rollbacks
    p = _make_proposal(db, reason="happy")
    db.add(BugFixCandidate(
        source_type="auto_rollback",
        source_ref=f"evolution_{p.id}",
        title="[Auto-Rollback]",
        status="applied",
    ))
    db.flush()
    summary = escalate_failed_rollbacks(db)
    assert summary["escalated"] == 0


# ---------------------------------------------------------------------------
# FIX 5 — Auto-extend loop
# ---------------------------------------------------------------------------

def test_auto_extend_creates_deeper_variant(db):
    from app.services.evolution_decision_engine import auto_extend_proposal
    parent = _make_proposal(
        db, reason="Winning cart conversion change",
        tech_outcome="inconclusive", business_outcome="improved",
        confidence_score=0.75,
    )
    child_id = auto_extend_proposal(db, parent)
    assert child_id is not None
    child = db.query(EvolutionProposal).filter(EvolutionProposal.id == child_id).first()
    assert child is not None
    assert child.extended_from_proposal_id == parent.id
    assert child.status == "open"
    assert child.risk_level == "LEVEL_2"
    assert str(parent.id) in child.reason
    assert child.dedup_key.startswith("auto_extend:")


def test_auto_extend_blocks_duplicate_child(db):
    from app.services.evolution_decision_engine import auto_extend_proposal
    parent = _make_proposal(
        db, reason="dup parent",
        tech_outcome="inconclusive", business_outcome="improved",
        confidence_score=0.75,
    )
    first = auto_extend_proposal(db, parent)
    second = auto_extend_proposal(db, parent)
    assert first is not None
    assert second is None  # dedup — one child per parent


def test_auto_extend_refuses_low_confidence(db):
    from app.services.evolution_decision_engine import auto_extend_proposal
    parent = _make_proposal(
        db, reason="weak signal",
        tech_outcome="inconclusive", business_outcome="improved",
        confidence_score=0.4,  # below _EXTEND_MIN_CONFIDENCE (0.60)
    )
    assert auto_extend_proposal(db, parent) is None


def test_auto_extend_refuses_non_improved_business_outcome(db):
    from app.services.evolution_decision_engine import auto_extend_proposal
    parent = _make_proposal(
        db, reason="stable signal",
        tech_outcome="effective", business_outcome="stable",
        confidence_score=0.9,
    )
    assert auto_extend_proposal(db, parent) is None


# ---------------------------------------------------------------------------
# run_decision_cycle integrates auto-extend on extend_carefully
# ---------------------------------------------------------------------------

def test_run_cycle_auto_extends_on_business_success(db):
    from app.services.evolution_decision_engine import run_decision_cycle
    with patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count", return_value=0,
    ):
        parent = _make_proposal(
            db, reason="winner to extend",
            tech_outcome="inconclusive", business_outcome="improved",
            confidence_score=0.75,
        )
        summary = run_decision_cycle(db)

    assert summary["extend_carefully"] >= 1
    assert summary.get("extended", 0) >= 1

    # Verify a child was created for our parent specifically
    child = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.extended_from_proposal_id == parent.id)
        .first()
    )
    assert child is not None
