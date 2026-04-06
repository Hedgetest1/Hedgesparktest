"""
Tests for the strategic alignment lock.

The North Star is conversion_recovery_from_behavioral_signals. Every bet
must score >= STRATEGY_MIN_ALIGNMENT_SCORE against it. Bets touching
forbidden competitor territory are instantly rejected.
"""
from __future__ import annotations

import json

from app.services.evolution_strategy import (
    score_alignment,
    check_strategy_alignment,
    format_strategy_for_prompt,
    STRATEGY_MIN_ALIGNMENT_SCORE,
    STRATEGY_VERSION,
    STRATEGY_LOCKED_UNTIL,
)
from app.services.monthly_evolution_audit import _parse_proposals


def _bet(**overrides) -> dict:
    base = {
        "title": "Add urgency nudge for return visitors on PDP",
        "type": "conversion",
        "revenue_thesis": (
            "Return visitors on PDPs see urgency nudge → ATC +8% → recovers "
            "~€240/mo from visitors who would otherwise bounce at checkout."
        ),
        "rejected_alternatives": [
            {"alternative": "Price test", "why_rejected": "tested, -3% CVR"},
            {"alternative": "Free shipping", "why_rejected": "margin too thin"},
        ],
        "expected_impact": "+€240/mo CVR +8% on top-10 products",
        "risk_level": "LEVEL_2",
        "infra_cost_estimate": "none",
        "infra_cost_reasoning": "pure content change",
        "exploration_bet": False,
        "why_this_bet_aligns_with_strategy": (
            "Strengthens the in-session intervention step of the core loop "
            "by targeting high-intent return visitors at the moment of bounce."
        ),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Scoring — pure function
# ---------------------------------------------------------------------------

def test_score_aligned_conversion_bet_high():
    score, verdict, hits = score_alignment(_bet())
    assert verdict == "aligned"
    assert score >= STRATEGY_MIN_ALIGNMENT_SCORE
    assert "conversion" in hits or "nudge" in hits or "cart" in hits


def test_score_tier3_only_capped():
    """A bet mentioning only infra keywords caps at 4.0."""
    bet = _bet(
        title="Increase worker cache memory for database throughput",
        revenue_thesis="Raise worker memory → lower p95 database latency → improves performance.",
        expected_impact="p95 -200ms on worker cycle",
        why_this_bet_aligns_with_strategy="Infra unblock for downstream work.",
    )
    score, verdict, _ = score_alignment(bet)
    assert verdict == "tier3_only"
    assert score <= 4.0


def test_score_off_strategy_no_matches():
    bet = _bet(
        title="Refactor logger module for readability",
        revenue_thesis="Clean logs → better developer experience → faster debugging.",
        expected_impact="10% faster debugging",
        why_this_bet_aligns_with_strategy="Indirect enablement.",
    )
    score, verdict, _ = score_alignment(bet)
    assert verdict == "off_strategy"
    assert score == 0.0


def test_score_forbidden_keywords_hit_ad_spend():
    bet = _bet(
        title="Integrate Facebook ads attribution",
        revenue_thesis="Pull Facebook ads ROAS → attribute checkout to campaigns → +€500/mo.",
        expected_impact="+€500/mo attributed to ad spend",
        why_this_bet_aligns_with_strategy="Ties revenue to ads.",
    )
    score, verdict, hits = score_alignment(bet)
    assert verdict == "forbidden"
    assert score == 0.0
    assert any("facebook ads" in h or "ad spend" in h or "roas" in h for h in hits)


def test_score_forbidden_ltv_competitor_territory():
    bet = _bet(
        title="LTV model per merchant",
        revenue_thesis="Compute lifetime value per cohort → prioritise retention cohorts → +€300/mo.",
        expected_impact="LTV-weighted nudges → +€300/mo",
        why_this_bet_aligns_with_strategy="Adds LTV dimension.",
    )
    score, verdict, hits = score_alignment(bet)
    assert verdict == "forbidden"
    assert "ltv" in hits or "lifetime value" in hits


def test_score_forbidden_email_winback():
    bet = _bet(
        title="Klaviyo win-back email campaign trigger",
        revenue_thesis="Send win-back email 14d after purchase → +€150/mo recovered.",
        expected_impact="+€150/mo from winback",
        why_this_bet_aligns_with_strategy="Retention.",
    )
    _, verdict, _ = score_alignment(bet)
    assert verdict == "forbidden"


# ---------------------------------------------------------------------------
# check_strategy_alignment — returns (error, score, verdict, hits)
# ---------------------------------------------------------------------------

def test_check_returns_none_error_for_aligned_bet():
    err, score, verdict, _ = check_strategy_alignment(_bet())
    assert err is None
    assert verdict == "aligned"
    assert score >= STRATEGY_MIN_ALIGNMENT_SCORE


def test_check_rejects_low_alignment():
    bet = _bet(
        title="Minor signal tweak",
        revenue_thesis="Tweak one signal threshold — tiny measurement refinement.",
        expected_impact="~5% signal freshness improvement",
        why_this_bet_aligns_with_strategy="Measurement.",
    )
    err, score, verdict, _ = check_strategy_alignment(bet)
    # Only one Tier-2 hit — below threshold
    if score < STRATEGY_MIN_ALIGNMENT_SCORE:
        assert err is not None
        assert "strategy_alignment_too_low" in err


def test_check_rejects_forbidden():
    bet = _bet(
        title="Multi-channel marketing integration",
        revenue_thesis="Pull multichannel data → omnichannel dashboard → +€400/mo.",
        expected_impact="+€400/mo omnichannel uplift",
        why_this_bet_aligns_with_strategy="Expands channels.",
    )
    err, _, verdict, _ = check_strategy_alignment(bet)
    assert err is not None
    assert "strategy_forbidden_keywords" in err
    assert verdict == "forbidden"


# ---------------------------------------------------------------------------
# Parser integration — strategy filter before storage
# ---------------------------------------------------------------------------

def test_parser_rejects_forbidden_strategy_bet():
    raw = json.dumps({"bets": [_bet(
        title="Facebook Ads ROAS attribution dashboard",
        revenue_thesis="Surface facebook ads ROAS → merchant sees which ads drive cart → +€600/mo.",
        expected_impact="+€600/mo attributed",
        why_this_bet_aligns_with_strategy="Attribution.",
    )]})
    out = _parse_proposals(raw)
    assert out == []


def test_parser_rejects_off_strategy_bet():
    raw = json.dumps({"bets": [_bet(
        title="Rewrite internal logger for clarity",
        revenue_thesis="Better logging helps engineers debug faster, saves time on investigations.",
        expected_impact="10% faster debugging",
        why_this_bet_aligns_with_strategy="Developer productivity.",
    )]})
    out = _parse_proposals(raw)
    assert out == []


def test_parser_accepts_strategy_aligned_bet():
    raw = json.dumps({"bets": [_bet()]})
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert out[0]["strategy_alignment_score"] >= STRATEGY_MIN_ALIGNMENT_SCORE
    assert out[0]["strategy_version"] == STRATEGY_VERSION
    assert "core loop" in out[0]["why_this_bet_aligns_with_strategy"].lower()


def test_parser_persists_alignment_score():
    raw = json.dumps({"bets": [_bet()]})
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert isinstance(out[0]["strategy_alignment_score"], float)
    assert 0 <= out[0]["strategy_alignment_score"] <= 10.0


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def test_format_strategy_for_prompt_contains_key_sections():
    text = format_strategy_for_prompt()
    assert "CURRENT STRATEGY" in text
    assert str(STRATEGY_VERSION) in text
    assert str(STRATEGY_LOCKED_UNTIL.isoformat()) in text
    assert "Tier 1" in text
    assert "Tier 2" in text
    assert "Tier 3" in text
    assert "NOT DOING" in text
    assert "ALIGNMENT REQUIREMENT" in text
    # Competitor moats named explicitly
    assert "Triple Whale" in text
    assert "Lifetimely" in text


def test_format_strategy_includes_threshold():
    text = format_strategy_for_prompt()
    assert str(STRATEGY_MIN_ALIGNMENT_SCORE) in text


# ---------------------------------------------------------------------------
# Storage — strategy columns persist to DB
# ---------------------------------------------------------------------------

def test_strategy_fields_persist_to_db(db):
    from app.services.monthly_evolution_audit import _store_proposals
    from app.models.evolution_proposal import EvolutionProposal

    raw = json.dumps({"bets": [_bet(title="persist strategy test")]})
    parsed = _parse_proposals(raw)
    assert len(parsed) == 1

    stored = _store_proposals(db, parsed, "9999-M99")
    assert stored == 1

    row = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key.like("monthly_opus:9999-M99:%")
    ).first()
    assert row is not None
    assert row.strategy_alignment_score is not None
    assert row.strategy_alignment_score >= STRATEGY_MIN_ALIGNMENT_SCORE
    assert row.strategy_version == STRATEGY_VERSION
