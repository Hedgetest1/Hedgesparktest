"""
Tests for the revenue feedback loop.

Covers:
  1. classify_business_domain heuristic (conversion vs infra)
  2. _classify_delta trend-adjusted classification + sample-size gate
  3. combined_outcome_label — multi-dim outcome collapsing
  4. compute_priority_score — historical-driven prioritization
  5. should_reject_proposal — anti-bullshit filter
  6. propagate_business_outcomes — batch scan + DB write
  7. Pending window (settling period) — don't prematurely measure
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.evolution_proposal import EvolutionProposal
from app.services.evolution_business_outcomes import (
    classify_business_domain,
    _classify_delta,
    combined_outcome_label,
    compute_priority_score,
    should_reject_proposal,
    measure_business_impact,
    propagate_business_outcomes,
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_proposal(
    db, *, reason="t", target_file=None, risk="LEVEL_2", applied_at=None,
    outcome_status=None, business_outcome=None, cycle="9999-M99",
) -> EvolutionProposal:
    p = EvolutionProposal(
        proposal_type="architecture",
        risk_level=risk,
        reason=reason,
        expected_impact="impact",
        auto_applicable=False,
        status="accepted",
        audit_cycle=cycle,
        dedup_key=f"monthly_opus:{cycle}:{reason[:40]}",
        target_file=target_file,
        applied_at=applied_at,
        outcome_status=outcome_status,
        business_outcome=business_outcome,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# classify_business_domain
# ---------------------------------------------------------------------------

class _P:
    def __init__(self, reason="", expected_impact="", target_file=""):
        self.reason = reason
        self.expected_impact = expected_impact
        self.target_file = target_file


def test_classify_domain_conversion_by_keyword():
    assert classify_business_domain(_P(reason="Improve cart conversion rate")) == "conversion"
    assert classify_business_domain(_P(reason="Fix nudge targeting for return visitors")) == "conversion"
    assert classify_business_domain(_P(reason="Reduce funnel drop-off at checkout")) == "conversion"


def test_classify_domain_conversion_by_file():
    assert classify_business_domain(_P(target_file="app/services/attribution_engine.py")) == "conversion"
    assert classify_business_domain(_P(target_file="tracker/spark-nudge.js")) == "conversion"


def test_classify_domain_infra_by_default():
    assert classify_business_domain(_P(reason="Split ops.py into smaller files")) == "infra"
    assert classify_business_domain(_P(reason="Refactor worker cycle scheduler")) == "infra"
    assert classify_business_domain(_P(target_file="app/core/database.py")) == "infra"


# ---------------------------------------------------------------------------
# _classify_delta — trend-adjusted classification
# ---------------------------------------------------------------------------

def test_classify_delta_improved_trend_adjusted():
    # BEFORE baseline cvr=0.02; CONTROL cvr=0.019 → pre-trend +0.001 (0.1pp)
    # AFTER cvr=0.025 → raw delta +0.005, adjusted +0.004 (20% relative)
    before = {"cvr": 0.02, "rpv": 1.0, "visitors": 10_000, "orders": 200}
    after = {"cvr": 0.025, "rpv": 1.25, "visitors": 10_000, "orders": 250}
    control = {"cvr": 0.019, "rpv": 0.95, "visitors": 10_000, "orders": 190}
    outcome, detail = _classify_delta(before, after, control, min_orders=50, min_visitors=2000)
    assert outcome == "improved"
    assert detail["cvr_trend_adjusted_relative"] > 0.05


def test_classify_delta_declined_after_trend_removal():
    # Pre-trend was positive; but AFTER dropped. Trend-adjusted must show decline.
    before = {"cvr": 0.03, "rpv": 1.5, "visitors": 10_000, "orders": 300}
    after = {"cvr": 0.025, "rpv": 1.25, "visitors": 10_000, "orders": 250}
    control = {"cvr": 0.025, "rpv": 1.25, "visitors": 10_000, "orders": 250}
    outcome, _ = _classify_delta(before, after, control, min_orders=50, min_visitors=2000)
    assert outcome == "declined"


def test_classify_delta_stable_when_trend_explains_change():
    # AFTER looks better than BEFORE, but pre-trend was already rising the same amount.
    # Trend-adjusted delta → near zero → stable.
    before = {"cvr": 0.02, "rpv": 1.0, "visitors": 10_000, "orders": 200}
    after = {"cvr": 0.022, "rpv": 1.1, "visitors": 10_000, "orders": 220}
    control = {"cvr": 0.018, "rpv": 0.9, "visitors": 10_000, "orders": 180}
    outcome, _ = _classify_delta(before, after, control, min_orders=50, min_visitors=2000)
    assert outcome == "stable"


def test_classify_delta_inconclusive_on_small_sample():
    before = {"cvr": 0.02, "rpv": 1.0, "visitors": 100, "orders": 2}
    after = {"cvr": 0.05, "rpv": 2.5, "visitors": 100, "orders": 5}
    control = {"cvr": 0.02, "rpv": 1.0, "visitors": 100, "orders": 2}
    outcome, detail = _classify_delta(before, after, control, min_orders=50, min_visitors=2000)
    assert outcome == "inconclusive"
    assert detail["reason"] == "sample_too_small"


# ---------------------------------------------------------------------------
# combined_outcome_label
# ---------------------------------------------------------------------------

def test_combined_label_both():
    assert combined_outcome_label("effective", "improved") == "BOTH"


def test_combined_label_business_success_overrides():
    # Business win with tech inconclusive is still BUSINESS_SUCCESS.
    assert combined_outcome_label("inconclusive", "improved") == "BUSINESS_SUCCESS"
    assert combined_outcome_label(None, "improved") == "BUSINESS_SUCCESS"


def test_combined_label_tech_success_without_biz():
    assert combined_outcome_label("effective", "not_applicable") == "TECH_SUCCESS"
    assert combined_outcome_label("effective", "stable") == "TECH_SUCCESS"
    assert combined_outcome_label("effective", None) == "TECH_SUCCESS"
    assert combined_outcome_label("effective", "pending") == "TECH_SUCCESS"


def test_combined_label_neither():
    assert combined_outcome_label("ineffective", "declined") == "NEITHER"
    assert combined_outcome_label("ineffective", "stable") == "NEITHER"
    assert combined_outcome_label("effective", "declined") == "NEITHER"


def test_combined_label_noise():
    assert combined_outcome_label("inconclusive", "inconclusive") == "NOISE"
    assert combined_outcome_label(None, None) == "NOISE"


# ---------------------------------------------------------------------------
# compute_priority_score
# ---------------------------------------------------------------------------

def test_priority_score_high_when_category_proven():
    # conversion domain has 9 improved / 1 declined → 90% success rate
    rates = {
        "conversion": {"improved": 9, "declined": 1, "stable": 0, "total": 10, "success_rate": 0.9},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
    }
    p = _P(reason="Improve cart conversion rate")
    p.risk_level = "LEVEL_2"
    score = compute_priority_score(p, rates)
    # 60 * 0.9 * 1.0 + 30 + 10 = 94
    assert score["score"] >= 90
    assert score["breakdown"]["domain"] == "conversion"


def test_priority_score_low_when_no_history():
    rates = {
        "conversion": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
    }
    p = _P(reason="Refactor worker scheduler")
    p.risk_level = "LEVEL_3"
    score = compute_priority_score(p, rates)
    # 0 * ... + 15 + 3 = 18
    assert score["score"] == 18


def test_priority_score_sample_weight_dampens_few_samples():
    # 100% success rate but only 2 samples → sample_weight=0.2 → confidence = 12
    rates = {
        "conversion": {"improved": 2, "declined": 0, "stable": 0, "total": 2, "success_rate": 1.0},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
    }
    p = _P(reason="Improve checkout conversion")
    p.risk_level = "LEVEL_2"
    score = compute_priority_score(p, rates)
    # 60 * 1.0 * 0.2 + 30 + 10 = 52
    assert 50 <= score["score"] <= 55


# ---------------------------------------------------------------------------
# should_reject_proposal — anti-bullshit filter
# ---------------------------------------------------------------------------

def test_anti_bullshit_blocks_low_success_category():
    rates = {
        "conversion": {"improved": 1, "declined": 9, "stable": 0, "total": 10, "success_rate": 0.1},
        "infra": {"improved": 0, "declined": 0, "stable": 0, "total": 0, "success_rate": 0.0},
    }
    reject, reason = should_reject_proposal(
        {"reasoning": "Add a new nudge banner to cart page",
         "expected_impact": "Increase revenue"},
        rates,
    )
    assert reject is True
    assert "below threshold" in reason


def test_anti_bullshit_allows_exploration_on_cold_category():
    """Don't block proposals when we have no history — exploration must survive."""
    rates = {
        "conversion": {"improved": 0, "declined": 2, "stable": 0, "total": 2, "success_rate": 0.0},
    }
    reject, reason = should_reject_proposal(
        {"reasoning": "Add cart abandonment nudge"}, rates,
    )
    assert reject is False
    assert reason == "insufficient_history"


def test_anti_bullshit_allows_successful_category():
    rates = {
        "conversion": {"improved": 7, "declined": 3, "stable": 0, "total": 10, "success_rate": 0.7},
    }
    reject, _ = should_reject_proposal(
        {"reasoning": "Improve cart nudge targeting"}, rates,
    )
    assert reject is False


# ---------------------------------------------------------------------------
# measure_business_impact — window & pending logic
# ---------------------------------------------------------------------------

def test_measure_marks_infra_proposal_not_applicable(db):
    p = _make_proposal(
        db, reason="Split ops.py into smaller modules",
        target_file="app/api/ops.py", applied_at=_utcnow() - timedelta(days=30),
        outcome_status="effective",
    )
    outcome, evidence = measure_business_impact(db, p)
    assert outcome == "not_applicable"
    assert evidence["domain"] == "infra"


def test_measure_returns_pending_when_before_settling(db):
    p = _make_proposal(
        db, reason="Improve cart conversion nudge",
        applied_at=_utcnow() - timedelta(days=3),  # within settling+window
        outcome_status="effective",
    )
    outcome, evidence = measure_business_impact(db, p)
    assert outcome == "pending"
    assert "after-window" in evidence["reason"]


def test_measure_returns_pending_when_not_applied(db):
    p = _make_proposal(
        db, reason="Improve cart conversion nudge",
        applied_at=None, outcome_status="effective",
    )
    outcome, _ = measure_business_impact(db, p)
    assert outcome == "pending"


# ---------------------------------------------------------------------------
# propagate_business_outcomes — orchestration
# ---------------------------------------------------------------------------

def test_propagate_sets_not_applicable_on_infra(db):
    """Infra proposal with completed window → not_applicable persisted."""
    p = _make_proposal(
        db, reason="Refactor worker scheduler",
        target_file="app/workers/agent_worker.py",
        applied_at=_utcnow() - timedelta(days=30),
        outcome_status="effective",
    )
    summary = propagate_business_outcomes(db)
    db.flush()
    assert summary["not_applicable"] >= 1
    db.refresh(p)
    assert p.business_outcome == "not_applicable"
    assert p.business_measured_at is not None


def test_propagate_ignores_unmeasured_tech_proposals(db):
    """A proposal with outcome_status=NULL is not scanned yet."""
    _make_proposal(
        db, reason="Improve cart nudge",
        applied_at=_utcnow() - timedelta(days=30),
        outcome_status=None,
    )
    summary = propagate_business_outcomes(db)
    # Our new proposal is NOT in the scan set (outcome_status IS NULL).
    # Other proposals in the real DB with outcome_status set may be scanned,
    # so we only assert that OURS wasn't touched.
    # Explicit check: query it back and verify business_outcome still NULL.
    from app.models.evolution_proposal import EvolutionProposal
    ours = db.query(EvolutionProposal).filter(
        EvolutionProposal.reason == "Improve cart nudge",
        EvolutionProposal.outcome_status.is_(None),
    ).first()
    assert ours.business_outcome is None


def test_propagate_skips_already_measured(db):
    p = _make_proposal(
        db, reason="Improve cart conversion",
        applied_at=_utcnow() - timedelta(days=30),
        outcome_status="effective",
        business_outcome="improved",
    )
    # Run propagate — our proposal should be skipped.
    propagate_business_outcomes(db)
    db.refresh(p)
    assert p.business_outcome == "improved"  # untouched
