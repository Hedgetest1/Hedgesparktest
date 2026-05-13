"""
Unit tests for the pure helpers extracted from `generate_action_candidates`
in the 2026-05-12 A3 refactor (commit a42d18c).

End-to-end coverage exists via the `/actions/candidates/pro` endpoint
tests; this file is the structural unit gate for:
  - `_clamp` / `_rank_score` / `_source_systems` / `_build_reason`
  - `_build_signal_buckets` (signal → action mapping + PRICE_TEST injection)
  - 4 gates: `_gate_scarcity`, `_gate_price_test`, `_gate_retarget`, `_gate_flash`
  - `_apply_action_gates` composer

The ranking math (_rank_score) determines what merchants see at the top
of the Pro action panel — any silent drift there changes merchant
priorities. The 4 gates encode the conditions under which each action
type fires — those thresholds are the contract.
"""
from __future__ import annotations

import pytest

from app.services.action_candidates_engine import (
    _apply_action_gates,
    _build_reason,
    _build_signal_buckets,
    _clamp,
    _gate_flash,
    _gate_price_test,
    _gate_retarget,
    _gate_scarcity,
    _rank_score,
    _source_systems,
)


# ---------------------------------------------------------------------------
# _clamp
# ---------------------------------------------------------------------------


class TestClamp:
    def test_within_range(self):
        assert _clamp(0.5) == 0.5

    def test_below_floor_clamps_to_lo(self):
        assert _clamp(-1.0) == 0.0

    def test_above_ceiling_clamps_to_hi(self):
        assert _clamp(2.0) == 1.0

    def test_custom_bounds(self):
        assert _clamp(50, lo=0, hi=100) == 50
        assert _clamp(150, lo=0, hi=100) == 100
        assert _clamp(-50, lo=0, hi=100) == 0


# ---------------------------------------------------------------------------
# _rank_score
# ---------------------------------------------------------------------------


class TestRankScore:
    def test_canonical_formula(self):
        # urgency=100, confidence=1.0, expected_loss=2000 → loss_norm=1.0
        # base = 100*0.5 + 1.0*100*0.3 + 1.0*100*0.2 = 50 + 30 + 20 = 100
        # effectiveness_boost = 0 → final = 100
        assert _rank_score(urgency=100, confidence=1.0, expected_loss=2000) == 100.0

    def test_zero_score(self):
        assert _rank_score(urgency=0, confidence=0, expected_loss=0) == 0.0

    def test_loss_clamped_at_2000_eur(self):
        # loss > 2000 should saturate at loss_norm=1.0
        s1 = _rank_score(urgency=50, confidence=0.5, expected_loss=2000)
        s2 = _rank_score(urgency=50, confidence=0.5, expected_loss=10000)
        assert s1 == s2

    def test_effectiveness_boost_positive(self):
        base = _rank_score(urgency=50, confidence=0.5, expected_loss=1000)
        boosted = _rank_score(
            urgency=50, confidence=0.5, expected_loss=1000, effectiveness_boost=1.0,
        )
        # Boost is ±10 rank points
        assert boosted == pytest.approx(base + 10.0)

    def test_effectiveness_boost_negative(self):
        base = _rank_score(urgency=50, confidence=0.5, expected_loss=1000)
        penalized = _rank_score(
            urgency=50, confidence=0.5, expected_loss=1000, effectiveness_boost=-1.0,
        )
        assert penalized == pytest.approx(base - 10.0)

    def test_weighting_urgency_dominant(self):
        # urgency contributes 50% of base; confidence 30%; loss 20%
        urgency_only = _rank_score(urgency=100, confidence=0, expected_loss=0)
        confidence_only = _rank_score(urgency=0, confidence=1.0, expected_loss=0)
        loss_only = _rank_score(urgency=0, confidence=0, expected_loss=2000)
        assert urgency_only > confidence_only > loss_only


# ---------------------------------------------------------------------------
# _source_systems
# ---------------------------------------------------------------------------


class TestSourceSystems:
    def test_default_base_systems(self):
        assert _source_systems("RETARGET_HOT_TRAFFIC", False, False) == [
            "opportunity_signals", "product_metrics",
        ]

    def test_scarcity_adds_upd(self):
        result = _source_systems("SCARCITY_NUDGE", False, False)
        assert "unique_product_detection" in result

    def test_price_test_adds_pi(self):
        result = _source_systems("PRICE_TEST", False, False)
        assert "price_intelligence" in result

    def test_vps_flag_appends(self):
        result = _source_systems("RETARGET_HOT_TRAFFIC", has_vps=True, has_ml=False)
        assert "visitor_product_state" in result

    def test_ml_flag_appends(self):
        result = _source_systems("RETARGET_HOT_TRAFFIC", has_vps=False, has_ml=True)
        assert "market_lookup" in result

    def test_all_flags_compose(self):
        result = _source_systems("SCARCITY_NUDGE", has_vps=True, has_ml=True)
        assert set(result) == {
            "opportunity_signals", "product_metrics",
            "unique_product_detection", "visitor_product_state", "market_lookup",
        }


# ---------------------------------------------------------------------------
# _build_reason
# ---------------------------------------------------------------------------


class TestBuildReason:
    def test_scarcity_reason(self):
        metrics = {"avg_scroll_24h": 80}
        upd = {"uniqueness_score": 85}
        result = _build_reason("SCARCITY_NUDGE", metrics, {}, upd)
        assert "80%" in result  # scroll
        assert "85" in result  # uniqueness
        assert "scarcity" in result.lower()

    def test_scarcity_fallback_when_no_scroll(self):
        metrics = {"avg_scroll_24h": 0}
        upd = {"uniqueness_score": 75}
        result = _build_reason("SCARCITY_NUDGE", metrics, {}, upd)
        assert "deep scroll engagement" in result

    def test_price_test_reason_with_explanation(self):
        pi = {
            "price_position": "POSSIBLY_TOO_HIGH",
            "confidence_score": 78,
            "intelligence_explanation": "Outpriced 3 competitors by 15%.",
        }
        result = _build_reason("PRICE_TEST", {}, pi, {})
        assert "POSSIBLY_TOO_HIGH" in result
        assert "78%" in result
        assert "Outpriced" in result

    def test_price_test_reason_without_explanation(self):
        pi = {"price_position": "REVIEW_NEEDED", "confidence_score": 70}
        result = _build_reason("PRICE_TEST", {}, pi, {})
        assert "REVIEW_NEEDED" in result
        assert "70%" in result

    def test_retarget_reason(self):
        metrics = {"return_visitor_count_7d": 12, "cart_conversions_24h": 0}
        result = _build_reason("RETARGET_HOT_TRAFFIC", metrics, {}, {})
        assert "12 returning visitors" in result

    def test_flash_reason(self):
        metrics = {"views_1h": 50, "views_24h": 600, "cart_conversions_24h": 4}
        result = _build_reason("FLASH_INCENTIVE", metrics, {}, {})
        assert "50 views" in result
        assert "600 total" in result

    def test_unknown_action_returns_default(self):
        result = _build_reason("UNKNOWN_ACTION", {}, {}, {})
        assert result == (
            "Multiple converging signals indicate an immediate action opportunity."
        )


# ---------------------------------------------------------------------------
# _build_signal_buckets
# ---------------------------------------------------------------------------


class TestBuildSignalBuckets:
    def test_maps_signal_to_action(self):
        signals = [{
            "product_url": "/products/x",
            "signal_type": "HIGH_ENGAGEMENT_NO_ACTION",
            "signal_strength": 0.7,
            "explanation": "deep scroll",
        }]
        buckets = _build_signal_buckets(signals, {}, {})
        assert ("/products/x", "SCARCITY_NUDGE") in buckets
        b = buckets[("/products/x", "SCARCITY_NUDGE")]
        assert b["signal_strength"] == 0.7
        assert "HIGH_ENGAGEMENT_NO_ACTION" in b["supporting_signals"]

    def test_unknown_signal_type_dropped(self):
        signals = [{
            "product_url": "/products/x",
            "signal_type": "NOT_A_KNOWN_SIGNAL",
            "signal_strength": 0.9,
            "explanation": "",
        }]
        buckets = _build_signal_buckets(signals, {}, {})
        assert buckets == {}

    def test_multiple_signals_to_same_action_merge_max_strength(self):
        signals = [
            {"product_url": "/products/x", "signal_type": "HIGH_ENGAGEMENT_NO_ACTION",
             "signal_strength": 0.5, "explanation": ""},
            {"product_url": "/products/x", "signal_type": "SCROLL_HIGH_NO_CLICK",
             "signal_strength": 0.9, "explanation": ""},
        ]
        buckets = _build_signal_buckets(signals, {}, {})
        b = buckets[("/products/x", "SCARCITY_NUDGE")]
        assert b["signal_strength"] == 0.9
        assert "HIGH_ENGAGEMENT_NO_ACTION" in b["supporting_signals"]
        assert "SCROLL_HIGH_NO_CLICK" in b["supporting_signals"]

    def test_price_test_injected_when_gate_passes(self):
        # POSSIBLY_TOO_HIGH + confidence>=65 + views_24h>=15
        metrics_map = {"/products/x": {"views_24h": 50}}
        pi_map = {"/products/x": {
            "price_position": "POSSIBLY_TOO_HIGH",
            "confidence_score": 75,
        }}
        buckets = _build_signal_buckets([], metrics_map, pi_map)
        assert ("/products/x", "PRICE_TEST") in buckets
        b = buckets[("/products/x", "PRICE_TEST")]
        assert b["signal_strength"] == 0.75
        assert "PRICE_FRICTION" in b["supporting_signals"]

    def test_price_test_blocked_by_low_confidence(self):
        # confidence < 65 → no inject
        pi_map = {"/products/x": {
            "price_position": "POSSIBLY_TOO_HIGH", "confidence_score": 60,
        }}
        metrics_map = {"/products/x": {"views_24h": 50}}
        buckets = _build_signal_buckets([], metrics_map, pi_map)
        assert ("/products/x", "PRICE_TEST") not in buckets

    def test_price_test_blocked_by_low_views(self):
        pi_map = {"/products/x": {
            "price_position": "POSSIBLY_TOO_HIGH", "confidence_score": 75,
        }}
        metrics_map = {"/products/x": {"views_24h": 10}}
        buckets = _build_signal_buckets([], metrics_map, pi_map)
        assert ("/products/x", "PRICE_TEST") not in buckets

    def test_price_test_blocked_by_wrong_position(self):
        pi_map = {"/products/x": {
            "price_position": "OK", "confidence_score": 90,
        }}
        metrics_map = {"/products/x": {"views_24h": 50}}
        buckets = _build_signal_buckets([], metrics_map, pi_map)
        assert ("/products/x", "PRICE_TEST") not in buckets


# ---------------------------------------------------------------------------
# 4 gates
# ---------------------------------------------------------------------------


class TestGateScarcity:
    def test_passes_when_unique_likely_and_score_high(self):
        b = {"signal_strength": 0.8, "supporting_signals": []}
        upd = {"uniqueness_status": "UNIQUE_LIKELY", "uniqueness_score": 90}
        result = _gate_scarcity(b, {}, {}, upd)
        assert result is not None
        confidence, urgency = result
        # confidence = (0.8 + 0.9) / 2 = 0.85
        assert confidence == pytest.approx(0.85)
        # urgency = clamp(0.8 * 80) = 64.0
        assert urgency == pytest.approx(64.0)

    def test_blocked_when_uniqueness_status_wrong(self):
        b = {"signal_strength": 0.9, "supporting_signals": []}
        upd = {"uniqueness_status": "UNCLEAR", "uniqueness_score": 90}
        assert _gate_scarcity(b, {}, {}, upd) is None

    def test_blocked_when_score_below_70(self):
        b = {"signal_strength": 0.9, "supporting_signals": []}
        upd = {"uniqueness_status": "UNIQUE_LIKELY", "uniqueness_score": 69}
        assert _gate_scarcity(b, {}, {}, upd) is None


class TestGatePriceTest:
    def test_canonical_confidence_and_urgency(self):
        # confidence_score=100 → confidence=1.0, urgency=(100-65)/35*60=60
        result = _gate_price_test({}, {}, {"confidence_score": 100}, {})
        assert result is not None
        confidence, urgency = result
        assert confidence == 1.0
        assert urgency == pytest.approx(60.0)

    def test_at_lower_threshold(self):
        # confidence=65 → urgency=0
        result = _gate_price_test({}, {}, {"confidence_score": 65}, {})
        assert result is not None
        _, urgency = result
        assert urgency == 0.0


class TestGateRetarget:
    def test_passes_when_returns_high_and_no_carts(self):
        metrics = {"return_visitor_count_7d": 10, "cart_conversions_24h": 0}
        b = {"signal_strength": 0.9, "supporting_signals": []}
        result = _gate_retarget(b, metrics, {}, {})
        assert result is not None
        confidence, urgency = result
        # confidence = clamp(0.9 * 0.9) = 0.81
        assert confidence == pytest.approx(0.81)
        # urgency = clamp(10 * 5, 0, 100) = 50
        assert urgency == 50.0

    def test_blocked_when_returns_below_5(self):
        metrics = {"return_visitor_count_7d": 4, "cart_conversions_24h": 0}
        assert _gate_retarget({"signal_strength": 0.9}, metrics, {}, {}) is None

    def test_blocked_when_cart_already_converting(self):
        metrics = {"return_visitor_count_7d": 20, "cart_conversions_24h": 1}
        assert _gate_retarget({"signal_strength": 0.9}, metrics, {}, {}) is None

    def test_urgency_saturates_at_80(self):
        # 100 returns × 5 = 500, clamped to min(returns*5, 80) = 80
        metrics = {"return_visitor_count_7d": 100, "cart_conversions_24h": 0}
        result = _gate_retarget({"signal_strength": 0.5}, metrics, {}, {})
        assert result is not None
        _, urgency = result
        assert urgency == 80.0


class TestGateFlash:
    def test_passes_when_strength_above_half(self):
        b = {"signal_strength": 0.6, "supporting_signals": []}
        result = _gate_flash(b, {}, {}, {})
        assert result is not None
        confidence, urgency = result
        # confidence = 0.6 * 0.85 = 0.51
        assert confidence == pytest.approx(0.51)
        # urgency is 0.0 — overwritten at enrichment
        assert urgency == 0.0

    def test_blocked_below_threshold(self):
        b = {"signal_strength": 0.49, "supporting_signals": []}
        assert _gate_flash(b, {}, {}, {}) is None


# ---------------------------------------------------------------------------
# _apply_action_gates — composer
# ---------------------------------------------------------------------------


class TestApplyActionGates:
    def test_passing_gate_produces_raw_candidate(self):
        buckets = {
            ("/products/x", "FLASH_INCENTIVE"): {
                "signal_strength": 0.8, "supporting_signals": ["TRAFFIC_SPIKE"],
            },
        }
        supporting = {"metrics_map": {}, "pi_map": {}, "upd_map": {}}
        raw = _apply_action_gates(buckets, supporting)
        assert len(raw) == 1
        c = raw[0]
        assert c["product_url"] == "/products/x"
        assert c["action_type"] == "FLASH_INCENTIVE"
        assert c["supporting_signals"] == ["TRAFFIC_SPIKE"]
        # confidence pre-rounded to 4dp
        assert c["confidence"] == pytest.approx(0.68, abs=0.005)
        # internal refs populated
        assert "_metrics" in c and "_pi" in c and "_upd" in c

    def test_failing_gate_filtered_out(self):
        buckets = {
            ("/products/x", "FLASH_INCENTIVE"): {
                "signal_strength": 0.3, "supporting_signals": [],
            },
        }
        supporting = {"metrics_map": {}, "pi_map": {}, "upd_map": {}}
        raw = _apply_action_gates(buckets, supporting)
        assert raw == []

    def test_unknown_action_type_filtered_out(self):
        buckets = {
            ("/products/x", "NOT_A_REAL_ACTION"): {
                "signal_strength": 1.0, "supporting_signals": [],
            },
        }
        raw = _apply_action_gates(
            buckets, {"metrics_map": {}, "pi_map": {}, "upd_map": {}}
        )
        assert raw == []

    def test_supporting_signals_deduped_preserving_order(self):
        buckets = {
            ("/products/x", "SCARCITY_NUDGE"): {
                "signal_strength": 1.0,
                "supporting_signals": ["A", "B", "A", "C", "B"],
            },
        }
        supporting = {
            "metrics_map": {},
            "pi_map": {},
            "upd_map": {"/products/x": {
                "uniqueness_status": "UNIQUE_LIKELY", "uniqueness_score": 95,
            }},
        }
        raw = _apply_action_gates(buckets, supporting)
        assert raw[0]["supporting_signals"] == ["A", "B", "C"]
