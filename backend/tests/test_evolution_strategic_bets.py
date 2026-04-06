"""
Tests for the strategic-bet redesign — Monthly Opus as constrained CTO.

Covers:
  - MAX_PROPOSALS_PER_RUN cap (3, not 10)
  - Bets WITHOUT revenue_thesis are rejected
  - Bets with <2 rejected_alternatives are rejected
  - Expanded type enum (growth/retention/conversion/experiment/deprecate accepted)
  - infra_cost_estimate enum validation
  - Domain kill: retire domains with n>=10 and success_rate<15%
  - Exploration required: flagged when one domain holds >=50% of wins
  - Exploration floor: bet batch rejected if required but not satisfied
  - Storage persists the new fields
"""
from __future__ import annotations

import json

import pytest

from app.services.monthly_evolution_audit import (
    _parse_proposals,
    _store_proposals,
    MAX_PROPOSALS_PER_RUN,
)
from app.services.evolution_reinforcement import (
    get_retired_domains,
    exploration_required,
)
from app.models.evolution_proposal import EvolutionProposal


def _bet(**overrides) -> dict:
    base = {
        "title": "Improve cart conversion nudge",
        "type": "conversion",
        "revenue_thesis": "Return visitors on PDP → add-to-cart up 12% → +€300/mo.",
        "rejected_alternatives": [
            {"alternative": "Price A/B test", "why_rejected": "already tested, -3% CVR"},
            {"alternative": "Free shipping banner", "why_rejected": "margin too thin"},
        ],
        "expected_impact": "+€300/mo on top-10 products",
        "risk_level": "LEVEL_2",
        "infra_cost_estimate": "none",
        "infra_cost_reasoning": "pure content change",
        "exploration_bet": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# MAX_PROPOSALS_PER_RUN
# ---------------------------------------------------------------------------

def test_max_bets_is_three():
    assert MAX_PROPOSALS_PER_RUN == 3


def test_parser_caps_at_three_even_if_llm_returns_ten():
    # Each bet needs strategic alignment (Tier-1 keywords) + distinct
    # fingerprint to survive parser discipline, so we use realistic
    # conversion-loop variants.
    variants = [
        ("conversion", "Add urgency nudge on cart abandonment", "cart-abandon"),
        ("experiment", "A/B test checkout nudge copy for returning visitors", "checkout-nudge"),
        ("conversion", "Surface attribution evidence in PDP intent signals", "attribution-signal"),
        ("experiment", "Holdout-measure behavioral-leak nudge variant", "holdout-variant"),
        ("conversion", "Targeting change: nudge high-intent session only", "session-targeting"),
        ("experiment", "Causal measurement of add-to-cart nudge frequency", "atc-frequency"),
        ("conversion", "In-session cart recovery for return visitors", "session-recovery"),
        ("experiment", "Dwell-time based nudge trigger threshold tuning", "dwell-trigger"),
        ("conversion", "Funnel intervention at checkout step 2", "checkout-step-2"),
        ("experiment", "Behavioral leak detector for scroll-depth drop", "scroll-leak"),
    ]
    bets = []
    for i, (t, title, tag) in enumerate(variants):
        bets.append(_bet(
            title=f"{title} v{i}",
            type=t,
            revenue_thesis=(
                f"[{tag}] In-session nudge on conversion leak → "
                f"cart add-to-cart +{5+i}% → recovers €{(i+1)*80}/mo via holdout-measured intervention."
            ),
            expected_impact=f"+€{(i+1)*80}/mo CVR +{5+i}% on behavioural-leak intervention",
        ))
    raw = json.dumps({"bets": bets})
    out = _parse_proposals(raw)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# Required discipline — revenue_thesis + rejected_alternatives
# ---------------------------------------------------------------------------

def test_parser_rejects_bet_without_revenue_thesis():
    raw = json.dumps({"bets": [_bet(revenue_thesis="")]})
    assert _parse_proposals(raw) == []


def test_parser_rejects_bet_with_thin_revenue_thesis():
    raw = json.dumps({"bets": [_bet(revenue_thesis="fast!")]})
    assert _parse_proposals(raw) == []


def test_parser_rejects_bet_with_zero_alternatives():
    raw = json.dumps({"bets": [_bet(rejected_alternatives=[])]})
    assert _parse_proposals(raw) == []


def test_parser_rejects_bet_with_one_alternative():
    raw = json.dumps({"bets": [_bet(rejected_alternatives=[
        {"alternative": "X", "why_rejected": "y"},
    ])]})
    assert _parse_proposals(raw) == []


def test_parser_accepts_bet_with_two_alternatives():
    raw = json.dumps({"bets": [_bet()]})
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert len(out[0]["rejected_alternatives"]) == 2


# ---------------------------------------------------------------------------
# Expanded type enum
# ---------------------------------------------------------------------------

def test_parser_accepts_business_types():
    for t in ("growth", "retention", "conversion", "experiment", "deprecate"):
        raw = json.dumps({"bets": [_bet(type=t)]})
        out = _parse_proposals(raw)
        assert len(out) == 1
        assert out[0]["type"] == t


def test_parser_rejects_invalid_type_never_defaults():
    # Post-hardening: invalid types are REJECTED, not normalized.
    raw = json.dumps({"bets": [_bet(type="marketing")]})
    out = _parse_proposals(raw)
    assert out == []


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------

def test_parser_validates_cost_enum():
    raw = json.dumps({"bets": [_bet(infra_cost_estimate="astronomical")]})
    out = _parse_proposals(raw)
    assert out[0]["infra_cost_estimate"] == "none"


def test_parser_accepts_cost_enum_values():
    for cost in ("none", "small", "medium", "large"):
        raw = json.dumps({"bets": [_bet(infra_cost_estimate=cost)]})
        out = _parse_proposals(raw)
        assert out[0]["infra_cost_estimate"] == cost


# ---------------------------------------------------------------------------
# Legacy compat (proposals key instead of bets)
# ---------------------------------------------------------------------------

def test_parser_accepts_legacy_proposals_key():
    raw = json.dumps({"proposals": [_bet()]})
    out = _parse_proposals(raw)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Domain kill
# ---------------------------------------------------------------------------

def test_retired_domain_when_ten_samples_and_low_success():
    weights = {
        "conversion": {
            "wins": 1, "losses": 11, "neutral": 0, "total": 12,
            "success_rate": 0.083, "weight": 0.1, "dampened": False,
        },
        "infra": {
            "wins": 5, "losses": 5, "neutral": 0, "total": 10,
            "success_rate": 0.5, "weight": 0.5, "dampened": False,
        },
    }
    retired = get_retired_domains(weights)
    names = {r["domain"] for r in retired}
    assert "conversion" in names
    assert "infra" not in names


def test_no_retirement_below_ten_samples():
    """Even 0% success stays un-retired below the evidence threshold."""
    weights = {
        "conversion": {
            "wins": 0, "losses": 8, "neutral": 0, "total": 8,
            "success_rate": 0.0, "weight": 0.0, "dampened": False,
        },
    }
    assert get_retired_domains(weights) == []


def test_retirement_is_reversible_on_fresh_wins():
    """Crossing back above _UNKILL_MIN_SUCCESS_RATE drops retirement."""
    weights = {
        "conversion": {
            "wins": 4, "losses": 11, "neutral": 0, "total": 15,
            "success_rate": 0.267, "weight": 0.3, "dampened": False,
        },
    }
    # 0.267 >= 0.15 → not retired
    assert get_retired_domains(weights) == []


# ---------------------------------------------------------------------------
# Exploration requirement
# ---------------------------------------------------------------------------

def test_exploration_required_when_one_domain_dominates():
    weights = {
        "conversion": {
            "wins": 6, "losses": 1, "neutral": 0, "total": 7,
            "success_rate": 0.857, "weight": 0.9, "dampened": False,
        },
        "retention": {
            "wins": 2, "losses": 2, "neutral": 0, "total": 4,
            "success_rate": 0.5, "weight": 0.5, "dampened": False,
        },
    }
    required, dom = exploration_required(weights)
    assert required is True
    assert dom == "conversion"


def test_exploration_not_required_when_balanced():
    # 3 domains, no single one holds >=50% of total wins:
    # conversion=4/11 (36%), retention=5/11 (45%), growth=2/11 (18%)
    weights = {
        "conversion": {
            "wins": 4, "losses": 1, "neutral": 0, "total": 5,
            "success_rate": 0.8, "weight": 0.8, "dampened": False,
        },
        "retention": {
            "wins": 5, "losses": 2, "neutral": 0, "total": 7,
            "success_rate": 0.71, "weight": 0.75, "dampened": False,
        },
        "growth": {
            "wins": 2, "losses": 1, "neutral": 0, "total": 3,
            "success_rate": 0.67, "weight": 0.65, "dampened": True,
        },
    }
    required, _ = exploration_required(weights)
    assert required is False


def test_exploration_not_required_when_insufficient_wins():
    """Fewer than 4 total wins → no dominance claim yet."""
    weights = {
        "conversion": {
            "wins": 3, "losses": 0, "neutral": 0, "total": 3,
            "success_rate": 1.0, "weight": 1.0, "dampened": True,
        },
    }
    required, _ = exploration_required(weights)
    assert required is False


# ---------------------------------------------------------------------------
# Storage — new fields persist
# ---------------------------------------------------------------------------

def test_store_persists_new_fields(db):
    bets = [_bet(title="persist test", exploration_bet=True, infra_cost_estimate="small")]
    stored = _store_proposals(db, bets, "9999-M99")
    assert stored == 1

    row = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key.like("monthly_opus:9999-M99:%")
    ).first()
    assert row is not None
    assert row.revenue_thesis is not None
    assert "Return visitors" in row.revenue_thesis
    assert row.rejected_alternatives is not None
    parsed_alts = json.loads(row.rejected_alternatives)
    assert len(parsed_alts) == 2
    assert parsed_alts[0]["alternative"] == "Price A/B test"
    assert row.infra_cost_estimate == "small"
    assert row.exploration_bet is True
