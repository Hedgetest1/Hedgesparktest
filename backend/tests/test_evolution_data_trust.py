"""
Tests for the data trust layer — the last line of defense against
phantom signals (delayed webhooks, broken pixels, impossible values)
triggering autonomous rollbacks on healthy proposals.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_business_outcomes import (
    assess_data_quality,
    _classify_delta,
)
from app.services.evolution_decision_engine import decide_action, run_decision_cycle


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _window(visitors=10_000, atc=1_500, orders=200, revenue=10_000.0):
    cvr = (orders / visitors) if visitors > 0 else 0.0
    atc_rate = (atc / visitors) if visitors > 0 else 0.0
    rpv = (revenue / visitors) if visitors > 0 else 0.0
    aov = (revenue / orders) if orders > 0 else 0.0
    return {
        "visitors": visitors, "atc_visitors": atc, "orders": orders,
        "revenue": revenue, "cvr": round(cvr, 6), "atc_rate": round(atc_rate, 6),
        "rpv": round(rpv, 4), "aov": round(aov, 2),
    }


# ---------------------------------------------------------------------------
# assess_data_quality — pure
# ---------------------------------------------------------------------------

def test_quality_high_on_sane_windows():
    q, issues = assess_data_quality(
        _window(10_000, 1_500, 200, 10_000),
        _window(10_500, 1_580, 220, 11_000),
        _window(9_800, 1_460, 195, 9_750),
    )
    assert q == "HIGH"
    assert issues == []


def test_quality_low_on_empty_visitors_window():
    q, issues = assess_data_quality(
        _window(0, 0, 0, 0.0),  # pixel broken in before window
        _window(10_000, 1_500, 200, 10_000),
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "LOW"
    assert any("empty_visitors_window" in i for i in issues)


def test_quality_low_when_orders_exceed_visitors():
    """Impossible: more orders than visitors → tracking mismatch."""
    q, issues = assess_data_quality(
        _window(100, 20, 500, 25_000),  # 500 orders / 100 visitors
        _window(10_000, 1_500, 200, 10_000),
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "LOW"
    assert any("orders_exceed_visitors" in i for i in issues)


def test_quality_low_when_atc_exceeds_visitors():
    q, issues = assess_data_quality(
        _window(1_000, 5_000, 100, 5_000),  # 5000 atc from 1000 visitors
        _window(10_000, 1_500, 200, 10_000),
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "LOW"
    assert any("atc_exceeds_visitors" in i for i in issues)


def test_quality_low_on_impossibly_high_cvr():
    """50% CVR doesn't happen in real ecommerce — data is broken."""
    q, issues = assess_data_quality(
        _window(100, 50, 60, 3_000),  # 60% cvr
        _window(10_000, 1_500, 200, 10_000),
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "LOW"
    assert any("impossibly_high_cvr" in i for i in issues)


def test_quality_low_on_orders_without_tracking():
    """Orders exist but visitor pipeline is silent → broken pixel."""
    q, issues = assess_data_quality(
        _window(0, 0, 10, 500.0),
        _window(10_000, 1_500, 200, 10_000),
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "LOW"
    # Both empty_visitors AND orders_without_visitor_tracking should fire
    assert any("empty_visitors_window" in i for i in issues)


def test_quality_medium_on_traffic_cliff():
    """Visitors dropped 5x: not impossible but highly suspect."""
    q, issues = assess_data_quality(
        _window(10_000, 1_500, 200, 10_000),
        _window(1_500, 220, 30, 1_500),  # 0.15x ratio
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "MEDIUM"
    assert any("visitor_volume_shift" in i for i in issues)


def test_quality_medium_on_aov_jump():
    """AOV went from $50 to $500 — currency bug or test orders."""
    q, issues = assess_data_quality(
        _window(10_000, 1_500, 200, 10_000),   # aov=$50
        _window(10_000, 1_500, 200, 100_000),  # aov=$500 (10x)
        _window(10_000, 1_500, 200, 10_000),
    )
    assert q == "MEDIUM"
    assert any("aov_shift" in i for i in issues)


# ---------------------------------------------------------------------------
# _classify_delta — data quality gate integration
# ---------------------------------------------------------------------------

def test_classify_delta_forces_inconclusive_on_low_quality():
    # Delta LOOKS catastrophic, but the before window has impossible values
    # (orders > visitors) — outcome MUST be inconclusive, confidence 0.
    before = _window(100, 20, 500, 25_000)  # broken
    after = _window(10_000, 1_500, 50, 2_500)  # looks terrible
    control = _window(10_000, 1_500, 200, 10_000)
    outcome, detail = _classify_delta(
        before, after, control, min_orders=50, min_visitors=2_000,
    )
    assert outcome == "inconclusive"
    assert detail["data_quality"] == "LOW"
    assert detail["confidence_score"] == 0.0
    assert "reason" in detail and detail["reason"] == "data_quality_low"


def test_classify_delta_halves_confidence_on_medium():
    # Big traffic shift → MEDIUM quality → confidence must be halved
    before = _window(10_000, 1_500, 300, 15_000)   # cvr=0.03
    after = _window(2_500, 375, 75, 3_750)          # cvr=0.03, but 0.25x traffic
    control = _window(10_000, 1_500, 300, 15_000)  # cvr=0.03
    outcome, detail = _classify_delta(
        before, after, control, min_orders=50, min_visitors=2_000,
    )
    assert detail["data_quality"] == "MEDIUM"
    # Confidence halved (originally whatever it would be)
    # Hard-assert it's < 0.5 which would block the 0.70 action threshold.
    assert detail["confidence_score"] < 0.5


# ---------------------------------------------------------------------------
# decide_action — LOW quality hard-block
# ---------------------------------------------------------------------------

def test_decide_action_refuses_rollback_on_low_quality():
    # Would normally trigger rollback (NEITHER + high confidence)
    action = decide_action("ineffective", "declined", 0.90, data_quality="LOW")
    assert action == "observe"


def test_decide_action_refuses_reinforce_on_low_quality():
    action = decide_action("effective", "improved", 0.90, data_quality="LOW")
    assert action == "observe"


def test_decide_action_allows_medium_through():
    # MEDIUM is allowed through (confidence was already halved upstream).
    # If caller somehow still has confidence >= 0.70, action fires. This
    # is intentional — MEDIUM is suspect, not broken.
    action = decide_action("effective", "improved", 0.90, data_quality="MEDIUM")
    assert action == "reinforce"


def test_decide_action_high_quality_acts_normally():
    action = decide_action("ineffective", "declined", 0.90, data_quality="HIGH")
    assert action == "rollback_proposed"


def test_decide_action_no_quality_given_acts_normally():
    """Backward compat: no data_quality parameter → original behavior."""
    action = decide_action("ineffective", "declined", 0.90)
    assert action == "rollback_proposed"


# ---------------------------------------------------------------------------
# run_decision_cycle — reads data_quality from business_evidence
# ---------------------------------------------------------------------------

def test_cycle_blocks_rollback_when_evidence_shows_low_quality(db):
    """End-to-end: a proposal with business_outcome=declined + high
    confidence + data_quality=LOW must NOT have a rollback candidate
    created. It must stay at 'observe'."""
    evidence = {
        "classification": {
            "primary_signal": "cvr",
            "data_quality": "LOW",
            "data_quality_issues": ["before:orders_exceed_visitors"],
            "confidence_score": 0.0,
        },
    }
    p = EvolutionProposal(
        proposal_type="architecture",
        risk_level="LEVEL_2",
        reason="would be rolled back if data were trustworthy",
        expected_impact="impact",
        auto_applicable=False,
        status="accepted",
        audit_cycle="9999-M99",
        dedup_key="monthly_opus:9999-M99:data_trust_test",
        target_file="app/services/orchestrator_llm.py",
        applied_at=_utcnow() - timedelta(days=40),
        applied_commit_sha="deadbeef1234",
        outcome_status="ineffective",
        business_outcome="declined",
        business_measured_at=_utcnow(),
        business_evidence=json.dumps(evidence),
        # Note: confidence_score >= 0.70 alone WOULD trigger rollback,
        # but data_quality=LOW in the evidence must override.
        confidence_score=0.90,
    )
    db.add(p)
    db.flush()

    with patch(
        "app.services.evolution_decision_engine._increment_daily_rollback_count",
    ), patch(
        "app.services.evolution_decision_engine._daily_rollback_count", return_value=0,
    ):
        summary = run_decision_cycle(db)

    db.refresh(p)
    assert p.decision_status == "observe"
    assert p.rollback_candidate_id is None
    assert summary["rollback_proposed"] == 0
