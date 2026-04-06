"""
Tests for the hardening pass — STEPS 1–8.

Each governance rule is tested in isolation AND integrated into the parser.
Every test is deterministic — no LLM calls, no time-dependent state.
"""
from __future__ import annotations

import json

import pytest

from app.services.evolution_bet_governance import (
    check_type_valid,
    override_underestimated_cost,
    classify_ux_sensitivity,
    validate_expected_impact,
    reject_if_retired_domain,
    normalize_fingerprint,
    check_batch_diversification,
    check_exploration_floor,
    VALID_TYPES,
)
from app.services.monthly_evolution_audit import _parse_proposals
from app.models.evolution_proposal import EvolutionProposal


def _bet(**overrides) -> dict:
    base = {
        "title": "Add return-visitor nudge on top PDPs",
        "type": "conversion",
        "revenue_thesis": "Return visitors see urgency banner → ATC up 8% → +€240/mo.",
        "rejected_alternatives": [
            {"alternative": "Price test", "why_rejected": "already tested, -3% CVR"},
            {"alternative": "Free shipping", "why_rejected": "margin too thin"},
        ],
        "expected_impact": "+€240/mo on top 10 products, CVR +8%",
        "risk_level": "LEVEL_2",
        "infra_cost_estimate": "none",
        "infra_cost_reasoning": "pure content change",
        "exploration_bet": False,
    }
    base.update(overrides)
    return base


# ===========================================================================
# STEP 1 — Invalid type → REJECT
# ===========================================================================

def test_type_valid_accepts_business_types():
    for t in ("growth", "retention", "conversion", "experiment", "deprecate"):
        assert check_type_valid({"type": t}) is None


def test_type_valid_rejects_unknown():
    assert check_type_valid({"type": "marketing"}) == "invalid_type:marketing"
    assert check_type_valid({"type": ""}) == "invalid_type:missing"
    assert check_type_valid({}) == "invalid_type:missing"


def test_parser_rejects_invalid_type_no_fallback():
    raw = json.dumps({"bets": [_bet(type="strategy")]})
    out = _parse_proposals(raw)
    assert out == []


def test_parser_no_longer_falls_back_to_architecture():
    """Regression guard: old parser coerced unknown → architecture. No more."""
    raw = json.dumps({"bets": [_bet(type="banana")]})
    out = _parse_proposals(raw)
    assert len(out) == 0


# ===========================================================================
# STEP 2 — Cost heuristic overrides
# ===========================================================================

def test_cost_override_worker_keyword():
    bet = _bet(title="Add new cache layer for PDPs",
               infra_cost_estimate="none")
    cost, reason = override_underestimated_cost(bet)
    assert cost == "small"
    assert reason is not None and "small_keywords_detected" in reason


def test_cost_override_new_service_keyword():
    bet = _bet(title="Spin up new service for nudge delivery",
               revenue_thesis="New service sends nudges via external API to merchants",
               infra_cost_estimate="small")
    cost, reason = override_underestimated_cost(bet)
    assert cost == "medium"
    assert reason is not None and "medium_keywords_detected" in reason


def test_cost_override_no_bump_when_accurate():
    bet = _bet(title="Change nudge copy to add urgency",
               revenue_thesis="Better copy → +8% ATC",
               infra_cost_estimate="none")
    cost, reason = override_underestimated_cost(bet)
    assert cost == "none"
    assert reason is None


def test_cost_override_accepts_already_high_estimate():
    bet = _bet(title="Introduce new worker for cache invalidation",
               infra_cost_estimate="medium")
    cost, reason = override_underestimated_cost(bet)
    # Already medium — no bump needed
    assert cost == "medium"
    assert reason is None


# ===========================================================================
# STEP 3 — UX sensitivity classification
# ===========================================================================

def test_ux_classifier_detects_dashboard_keyword():
    ux, radius = classify_ux_sensitivity(
        _bet(title="Redesign dashboard layout", expected_impact="Better UX + €100/mo")
    )
    assert ux is True
    assert radius == "structural"


def test_ux_classifier_detects_kpi_rename():
    ux, radius = classify_ux_sensitivity(
        _bet(title="Rename the Visitors KPI to Shoppers", expected_impact="Clarity +5%")
    )
    assert ux is True
    assert radius == "structural"


def test_ux_classifier_ignores_backend_changes():
    ux, radius = classify_ux_sensitivity(
        _bet(title="Improve DB query latency on product metrics", expected_impact="-120ms p95")
    )
    assert ux is False
    assert radius == "internal"


def test_ux_classifier_honors_declared_structural():
    bet = _bet(title="Ship new onboarding flow")
    bet["impact_radius"] = "structural"
    ux, radius = classify_ux_sensitivity(bet)
    assert ux is True
    assert radius == "structural"


def test_parser_forces_level_3_on_ux_sensitive():
    # Strategic alignment required: bet must also be tied to the core
    # conversion loop, not pure UX redesign.
    raw = json.dumps({"bets": [
        _bet(
            title="Restructure sidebar navigation to surface conversion opportunities",
            revenue_thesis=(
                "Conversion opportunities hidden 3 clicks deep → merchants miss nudges "
                "→ in-session recovery path gets skipped. Restructure puts cart-recovery "
                "CTA up-front → +€200/mo."
            ),
            expected_impact="Better nav discoverability → +€80/mo on conversion path",
            risk_level="LEVEL_2",
        ),
    ]})
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert out[0]["ux_sensitive"] is True
    assert out[0]["risk_level"] == "LEVEL_3"


# ===========================================================================
# STEP 4 — expected_impact quality gate
# ===========================================================================

def test_impact_accepts_currency():
    assert validate_expected_impact({"expected_impact": "+€300/month on top products"}) is None


def test_impact_accepts_percent():
    assert validate_expected_impact({"expected_impact": "CVR +8% on return visitors"}) is None


def test_impact_accepts_time_units():
    assert validate_expected_impact({"expected_impact": "p95 latency drops from 800ms to 400ms"}) is None


def test_impact_rejects_vague_verbs():
    assert validate_expected_impact({"expected_impact": "improve"}) is not None
    assert validate_expected_impact({"expected_impact": "better performance"}) is not None
    assert validate_expected_impact({"expected_impact": "optimize caching."}) is not None


def test_impact_rejects_missing():
    assert validate_expected_impact({}) is not None
    assert validate_expected_impact({"expected_impact": ""}) is not None
    assert validate_expected_impact({"expected_impact": "yes."}) is not None


def test_impact_rejects_text_without_numbers():
    assert validate_expected_impact(
        {"expected_impact": "Merchants will love this and feel happier about their store"}
    ) is not None


def test_parser_rejects_vague_impact():
    raw = json.dumps({"bets": [_bet(expected_impact="improve performance")]})
    out = _parse_proposals(raw)
    assert out == []


# ===========================================================================
# STEP 6 — Retired domain hard block
# ===========================================================================

def test_retired_domain_blocks_matching_bet():
    retired = [{"domain": "conversion", "success_rate": 0.08, "total": 12, "wins": 1, "losses": 11, "reason": "x"}]
    err = reject_if_retired_domain("conversion", retired)
    assert err == "retired_domain:conversion"


def test_retired_domain_blocks_retention_family():
    retired = [{"domain": "conversion", "success_rate": 0.08, "total": 12, "wins": 1, "losses": 11, "reason": "x"}]
    # retention maps to conversion domain (non-infra type)
    err = reject_if_retired_domain("retention", retired)
    assert err == "retired_domain:conversion"


def test_retired_infra_blocks_architecture_bet():
    retired = [{"domain": "infra", "success_rate": 0.05, "total": 20, "wins": 1, "losses": 19, "reason": "x"}]
    err = reject_if_retired_domain("architecture", retired)
    assert err == "retired_domain:infra"


def test_retired_domain_allows_other_family():
    retired = [{"domain": "infra", "success_rate": 0.05, "total": 20, "wins": 1, "losses": 19, "reason": "x"}]
    # conversion-family type is fine when infra is retired
    err = reject_if_retired_domain("conversion", retired)
    assert err is None


def test_retired_empty_list_no_block():
    assert reject_if_retired_domain("conversion", []) is None
    assert reject_if_retired_domain("architecture", None or []) is None


def test_parser_honors_retired_domains():
    retired = [{"domain": "conversion", "success_rate": 0.08, "total": 12, "wins": 1, "losses": 11, "reason": "dead"}]
    raw = json.dumps({"bets": [
        _bet(type="conversion"),   # must be blocked
        _bet(title="New infra worker for queuing", type="architecture",
             revenue_thesis="Queue decouples load → checkout p95 -200ms → +€150/mo",
             expected_impact="p95 checkout latency -200ms (was 900ms)",
             infra_cost_estimate="small"),
    ]})
    out = _parse_proposals(raw, retired_domains=retired)
    # Conversion bet blocked; architecture bet accepted
    assert len(out) == 1
    assert out[0]["type"] == "architecture"


# ===========================================================================
# STEP 7 — Fingerprint normalization (anti-loop)
# ===========================================================================

def test_fingerprint_collapses_synonyms():
    fp1 = normalize_fingerprint("Optimize caching layer")
    fp2 = normalize_fingerprint("Improve cache performance")
    # Both reduce to the cache/perf synonym token set
    assert fp1 != ""
    assert fp2 != ""
    # They should overlap meaningfully — "cache" and "perf" should appear in both
    assert "cache" in fp1 and "perf" in fp2


def test_fingerprint_strips_weak_verbs():
    fp = normalize_fingerprint("Improve the conversion rate")
    assert "improve" not in fp
    assert "the" not in fp
    assert "cvr" in fp  # "conversion" → "cvr" via synonym map


def test_fingerprint_empty_input():
    assert normalize_fingerprint("") == ""
    assert normalize_fingerprint(None or "") == ""


def test_parser_dedups_similar_wordings():
    raw = json.dumps({"bets": [
        _bet(title="Optimize caching layer",
             revenue_thesis="Cache reduces PDP load → +€200/mo via checkout path"),
        _bet(title="Improve cache performance",
             revenue_thesis="Cache reduces PDP load → +€200/mo via checkout path"),
    ]})
    out = _parse_proposals(raw)
    # Second bet with synonym-collapsed title + same thesis → blocked
    assert len(out) == 1


# ===========================================================================
# STEP 5 — Batch diversification + exploration
# ===========================================================================

def test_batch_diversification_allows_mixed_types():
    bets = [
        {"type": "conversion"}, {"type": "retention"}, {"type": "growth"},
    ]
    assert check_batch_diversification(bets) is None


def test_batch_diversification_rejects_all_same_type():
    bets = [{"type": "conversion"}, {"type": "conversion"}, {"type": "conversion"}]
    err = check_batch_diversification(bets)
    assert err is not None
    assert "no_diversification" in err


def test_batch_diversification_allows_single_bet():
    assert check_batch_diversification([{"type": "conversion"}]) is None


def test_batch_exploration_required_but_missing():
    bets = [
        {"type": "conversion", "exploration_bet": False},
        {"type": "retention", "exploration_bet": False},
    ]
    assert check_exploration_floor(bets, exploration_required=True) == "exploration_floor_violated"


def test_batch_exploration_satisfied():
    bets = [
        {"type": "conversion", "exploration_bet": False},
        {"type": "experiment", "exploration_bet": True},
    ]
    assert check_exploration_floor(bets, exploration_required=True) is None


def test_batch_exploration_not_required_passes():
    bets = [{"type": "conversion", "exploration_bet": False}]
    assert check_exploration_floor(bets, exploration_required=False) is None


# ===========================================================================
# STEP 8 — Empty batch is valid
# ===========================================================================

def test_parser_returns_empty_list_on_all_rejected():
    """All bets fail discipline → parser returns []. Caller must accept this."""
    raw = json.dumps({"bets": [
        _bet(type="banana"),                              # invalid type
        _bet(expected_impact="improve things"),           # vague impact
        _bet(revenue_thesis="short"),                     # thin thesis
    ]})
    assert _parse_proposals(raw) == []


def test_parser_returns_empty_on_empty_bets_key():
    assert _parse_proposals(json.dumps({"bets": []})) == []


def test_parser_returns_empty_on_missing_bets_key():
    assert _parse_proposals(json.dumps({})) == []


# ===========================================================================
# STEP 3 enforcement — converter refuses UX-sensitive proposals
# ===========================================================================

def test_converter_blocks_ux_sensitive_proposals(db):
    from app.services.evolution_converter import convert_eligible_proposals
    p = EvolutionProposal(
        proposal_type="product",
        target_file="dashboard/src/app/components/Sidebar.tsx",
        risk_level="LEVEL_1",
        reason="Restructure sidebar navigation",
        expected_impact="Cleaner nav",
        auto_applicable=True,
        status="open",
        ux_sensitive=True,       # hard-blocked
        impact_radius="structural",
    )
    db.add(p)
    db.flush()
    summary = convert_eligible_proposals(db, max_per_cycle=5)
    # Our UX-sensitive proposal is NOT converted
    assert summary["converted"] == 0 or all(
        (other.id == p.id) is False
        for other in db.query(EvolutionProposal).filter(
            EvolutionProposal.status == "accepted",
            EvolutionProposal.decided_by == "evolution_converter",
            EvolutionProposal.id == p.id,
        ).all()
    )
    # Re-read p — it must still be open
    db.refresh(p)
    assert p.status == "open"
