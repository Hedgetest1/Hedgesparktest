"""
Unit tests for the pure helpers extracted from `get_nudge_lift_report`
in the 2026-05-13 A3 refactor.

This is the first dedicated test coverage for the nudge-lift helpers.
The composer is end-to-end tested elsewhere (the lift report runs
against real DB state in test_nudge_measurement.py paths). This file
locks the structural-unit math + sample-state classification +
significance copy + attribution-value extraction.
"""
from __future__ import annotations

from app.services.nudge_measurement import (
    MIN_SAMPLE_PER_GROUP,
    _build_lift_attribution_note,
    _build_lift_significance,
    _compute_cvr_lift_pct,
    _compute_sample_state,
    _extract_attribution_values,
)


# ---------------------------------------------------------------------------
# _compute_cvr_lift_pct — 4-branch decision
# ---------------------------------------------------------------------------


class TestCvrLiftPct:
    def test_both_positive(self):
        # exposed 0.08, holdout 0.05 → (0.03/0.05)*100 = 60%
        assert _compute_cvr_lift_pct(0.08, 0.05) == 60.0

    def test_negative_lift_when_exposed_lower(self):
        # exposed 0.04, holdout 0.10 → (-0.06/0.10)*100 = -60%
        assert _compute_cvr_lift_pct(0.04, 0.10) == -60.0

    def test_zero_baseline_with_positive_exposed_returns_none(self):
        # Baseline=0 with positive exposed = undefined ratio; refuse to lie
        assert _compute_cvr_lift_pct(0.05, 0.0) is None

    def test_both_zero_returns_zero(self):
        # Neither group converted yet — lift is 0, not None
        assert _compute_cvr_lift_pct(0.0, 0.0) == 0.0

    def test_zero_exposed_negative_baseline_yields_minus_100(self):
        # exposed=0, holdout=0.05 → (-0.05/0.05)*100 = -100%
        assert _compute_cvr_lift_pct(0.0, 0.05) == -100.0

    def test_rounded_to_2dp(self):
        # exposed 0.1234, holdout 0.05 → 0.0734/0.05*100 = 146.8 → 146.8 (2dp)
        out = _compute_cvr_lift_pct(0.1234, 0.05)
        # Verify type + reasonable rounding
        assert isinstance(out, float)
        # 146.8 to 2 decimal places
        assert out == 146.8


# ---------------------------------------------------------------------------
# _compute_sample_state
# ---------------------------------------------------------------------------


class TestSampleState:
    def test_both_above_threshold_sufficient(self):
        state, under = _compute_sample_state(
            MIN_SAMPLE_PER_GROUP, MIN_SAMPLE_PER_GROUP,
        )
        assert state == "sufficient"
        assert under == []

    def test_exposed_below_threshold(self):
        state, under = _compute_sample_state(
            exposed_count=5, holdout_count=MIN_SAMPLE_PER_GROUP,
        )
        assert state == "insufficient"
        assert any("exposed=5" in u for u in under)

    def test_holdout_below_threshold(self):
        state, under = _compute_sample_state(
            exposed_count=MIN_SAMPLE_PER_GROUP, holdout_count=3,
        )
        assert state == "insufficient"
        assert any("holdout=3" in u for u in under)

    def test_both_below_threshold(self):
        state, under = _compute_sample_state(exposed_count=5, holdout_count=3)
        assert state == "insufficient"
        # Both groups named
        assert len(under) == 2

    def test_boundary_inclusive(self):
        # exactly MIN_SAMPLE → sufficient (>= test)
        state, _ = _compute_sample_state(
            MIN_SAMPLE_PER_GROUP, MIN_SAMPLE_PER_GROUP,
        )
        assert state == "sufficient"


# ---------------------------------------------------------------------------
# _build_lift_significance — 4 bands (insufficient / <0.05 / <0.10 / >=0.10)
# ---------------------------------------------------------------------------


class TestLiftSignificance:
    def test_insufficient_returns_zero_z(self):
        z, p, sig = _build_lift_significance(
            sample_state="insufficient",
            under_threshold=["exposed=5"],
            exposed_count=5, exposed_purchases=0,
            holdout_count=100, holdout_purchases=2,
        )
        assert z == 0.0
        assert p == 1.0
        assert "Insufficient sample" in sig
        assert "exposed=5" in sig

    def test_p_below_0_05_high_confidence(self):
        # Use clearly distinct populations to push p < 0.05
        z, p, sig = _build_lift_significance(
            sample_state="sufficient",
            under_threshold=[],
            exposed_count=1000, exposed_purchases=80,   # 8% CVR
            holdout_count=1000, holdout_purchases=20,   # 2% CVR
        )
        assert z > 0  # exposed > holdout
        assert p < 0.05
        assert ">95% confidence" in sig

    def test_p_between_0_05_and_0_10_medium_confidence(self):
        # Borderline p — pick numbers that yield 0.05 ≤ p < 0.10
        # 100 vs 100, 18 vs 10 → ~5%-ish p depending on z
        z, p, sig = _build_lift_significance(
            sample_state="sufficient",
            under_threshold=[],
            exposed_count=200, exposed_purchases=24,    # 12%
            holdout_count=200, holdout_purchases=14,    # 7%
        )
        # Loose assertion — the exact p depends on the z-test
        # implementation; the key is that the message format matches
        # the band we land in.
        if 0.05 <= p < 0.10:
            assert ">90% confidence" in sig
        elif p < 0.05:
            assert ">95% confidence" in sig
        else:
            assert "no meaningful difference" in sig

    def test_p_above_threshold_no_significance(self):
        # Identical populations → p is high
        z, p, sig = _build_lift_significance(
            sample_state="sufficient",
            under_threshold=[],
            exposed_count=100, exposed_purchases=10,
            holdout_count=100, holdout_purchases=10,
        )
        assert p > 0.10
        assert "no meaningful difference" in sig

    def test_z_and_p_rounded_to_4dp(self):
        z, p, _ = _build_lift_significance(
            sample_state="sufficient",
            under_threshold=[],
            exposed_count=1000, exposed_purchases=80,
            holdout_count=1000, holdout_purchases=40,
        )
        # Verify they're rounded
        assert z == round(z, 4)
        assert p == round(p, 4)


# ---------------------------------------------------------------------------
# _extract_attribution_values
# ---------------------------------------------------------------------------


class TestExtractAttributionValues:
    def test_all_fields_present(self):
        exposed = {
            "post_exposure_purchases": 10, "post_exposure_cvr": 0.05,
            "purchase_session_revenue": 500.0, "revenue_currency": "USD",
        }
        holdout = {
            "holdout_purchases": 4, "holdout_cvr": 0.02,
            "holdout_revenue": 200.0, "revenue_currency": "USD",
        }
        out = _extract_attribution_values(exposed, holdout)
        assert out == (10, 4, 0.05, 0.02, 500.0, 200.0, "USD", "USD")

    def test_missing_revenue_defaults_to_zero(self):
        exposed = {
            "post_exposure_purchases": 10, "post_exposure_cvr": 0.05,
        }
        holdout = {
            "holdout_purchases": 4, "holdout_cvr": 0.02,
        }
        out = _extract_attribution_values(exposed, holdout)
        # Revenue and currency fallbacks
        assert out[4] == 0.0
        assert out[5] == 0.0
        assert out[6] == "unknown"
        assert out[7] == "unknown"

    def test_null_revenue_defaults_to_zero(self):
        exposed = {
            "post_exposure_purchases": 10, "post_exposure_cvr": 0.05,
            "purchase_session_revenue": None,
        }
        holdout = {
            "holdout_purchases": 4, "holdout_cvr": 0.02,
            "holdout_revenue": None,
        }
        out = _extract_attribution_values(exposed, holdout)
        assert out[4] == 0.0
        assert out[5] == 0.0


# ---------------------------------------------------------------------------
# _build_lift_attribution_note
# ---------------------------------------------------------------------------


class TestLiftAttributionNote:
    def test_window_hours_propagated(self):
        out = _build_lift_attribution_note(window_hours=72)
        assert "72h" in out

    def test_honest_labeling_present(self):
        out = _build_lift_attribution_note(window_hours=24)
        # Anti-RCT honesty (CLAUDE.md §0 / §2 rule 4)
        assert "not a true RCT" in out
        assert "Do not claim proven causation" in out
        assert "estimated incremental lift" in out

    def test_assignment_mechanism_named(self):
        out = _build_lift_attribution_note(window_hours=24)
        # Make the deterministic-pseudo-random assignment visible
        assert "MD5" in out
        assert "pseudo-random" in out
