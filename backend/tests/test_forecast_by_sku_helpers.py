"""
Unit tests for the pure helpers extracted from `forecast_by_sku`
in the 2026-05-13 A3 refactor.

The composer is locked by test_forecast_by_sku_composer.py. This
file is the structural-unit gate — a regression in any helper
fails here before the composer test even runs.

Helpers covered:
    _clamp_sku_forecast_params
    _insufficient_product_record
    _classify_direction
    _compute_accuracy_pct
    _pick_riser_faller
    _build_forecast_insight
"""
from __future__ import annotations

from app.services.probabilistic_forecast import (
    _build_forecast_insight,
    _classify_direction,
    _clamp_sku_forecast_params,
    _compute_accuracy_pct,
    _insufficient_product_record,
    _pick_riser_faller,
)


# ---------------------------------------------------------------------------
# _clamp_sku_forecast_params — input bounds gate
# ---------------------------------------------------------------------------


class TestClampParams:
    def test_typical_inputs_pass_through(self):
        assert _clamp_sku_forecast_params(14, 60, 10) == (14, 60, 10)

    def test_horizon_clamped_low(self):
        assert _clamp_sku_forecast_params(0, 60, 10)[0] == 1
        assert _clamp_sku_forecast_params(-5, 60, 10)[0] == 1

    def test_horizon_clamped_high(self):
        assert _clamp_sku_forecast_params(100, 60, 10)[0] == 60
        assert _clamp_sku_forecast_params(60, 60, 10)[0] == 60

    def test_window_clamped_low(self):
        assert _clamp_sku_forecast_params(14, 1, 10)[1] == 7
        assert _clamp_sku_forecast_params(14, 6, 10)[1] == 7

    def test_window_clamped_high(self):
        assert _clamp_sku_forecast_params(14, 1000, 10)[1] == 365
        assert _clamp_sku_forecast_params(14, 365, 10)[1] == 365

    def test_top_n_clamped_low(self):
        assert _clamp_sku_forecast_params(14, 60, 0)[2] == 1

    def test_top_n_clamped_high(self):
        assert _clamp_sku_forecast_params(14, 60, 100)[2] == 25


# ---------------------------------------------------------------------------
# _insufficient_product_record — honest cold-start
# ---------------------------------------------------------------------------


class TestInsufficientRecord:
    def test_record_shape(self):
        out = _insufficient_product_record("p1", "Widget", 50.0, 3)
        assert out["product_key"] == "p1"
        assert out["title"] == "Widget"
        assert out["observed_revenue"] == 50.0
        assert out["confidence"] == "insufficient"
        assert out["forecast_point"] == 0.0
        assert out["forecast_lower_80"] == 0.0
        assert out["forecast_upper_80"] == 0.0
        assert out["forecast_lower_95"] == 0.0
        assert out["forecast_upper_95"] == 0.0
        assert out["delta_pct"] == 0.0
        assert out["direction"] == "stable"
        assert out["accuracy_pct"] is None
        assert out["n_days"] == 3
        assert out["r2"] == 0.0

    def test_product_key_truncated_to_128(self):
        long_key = "x" * 200
        out = _insufficient_product_record(long_key, "T", 0.0, 0)
        assert len(out["product_key"]) == 128

    def test_title_truncated_to_128(self):
        long_title = "T" * 200
        out = _insufficient_product_record("p1", long_title, 0.0, 0)
        assert len(out["title"]) == 128


# ---------------------------------------------------------------------------
# _classify_direction — ±5% deadband
# ---------------------------------------------------------------------------


class TestClassifyDirection:
    def test_above_5_pct_is_rising(self):
        assert _classify_direction(5.1) == "rising"
        assert _classify_direction(50.0) == "rising"

    def test_below_minus_5_pct_is_falling(self):
        assert _classify_direction(-5.1) == "falling"
        assert _classify_direction(-50.0) == "falling"

    def test_within_deadband_is_stable(self):
        assert _classify_direction(0.0) == "stable"
        assert _classify_direction(5.0) == "stable"
        assert _classify_direction(-5.0) == "stable"
        assert _classify_direction(4.9) == "stable"
        assert _classify_direction(-4.9) == "stable"


# ---------------------------------------------------------------------------
# _compute_accuracy_pct — MAPE-based backtest scalar
# ---------------------------------------------------------------------------


class TestAccuracyPct:
    def test_returns_none_when_too_few_points(self):
        assert _compute_accuracy_pct([10.0], [10.0]) is None
        assert _compute_accuracy_pct([], []) is None

    def test_returns_none_when_no_positive_observed(self):
        assert _compute_accuracy_pct([0.0, 0.0, 0.0], [0.0, 0.0, 0.0]) is None

    def test_perfect_fit_yields_100(self):
        out = _compute_accuracy_pct([10.0, 20.0, 30.0], [10.0, 20.0, 30.0])
        assert out == 100.0

    def test_typical_partial_fit(self):
        # observed=10, fitted=11 → ape=10%; obs=20, fit=22 → ape=10%; obs=30, fit=33 → 10%
        out = _compute_accuracy_pct([10.0, 20.0, 30.0], [11.0, 22.0, 33.0])
        assert out == 90.0

    def test_floor_clamp_to_zero(self):
        # Massively wrong fit → ape >> 100%, should clamp to 0.0
        out = _compute_accuracy_pct([1.0, 1.0], [50.0, 50.0])
        assert out == 0.0

    def test_zero_observed_skipped(self):
        # observed=[0,10,0,20], fitted=[5,11,3,22]
        # Only positive obs count: ape on (10,11)=10%, ape on (20,22)=10% → 90%
        out = _compute_accuracy_pct([0.0, 10.0, 0.0, 20.0], [5.0, 11.0, 3.0, 22.0])
        assert out == 90.0


# ---------------------------------------------------------------------------
# _pick_riser_faller — biggest_riser / biggest_faller selection
# ---------------------------------------------------------------------------


class TestPickRiserFaller:
    def test_empty_returns_four_nones(self):
        assert _pick_riser_faller([]) == (None, None, None, None)

    def test_riser_only(self):
        forecastable = [
            {"product_key": "a", "title": "A", "delta_pct": 10.0},
            {"product_key": "b", "title": "B", "delta_pct": 2.0},
        ]
        riser, faller, best, worst = _pick_riser_faller(forecastable)
        assert riser == {"product_key": "a", "title": "A", "delta_pct": 10.0}
        assert faller is None
        assert best["product_key"] == "a"
        assert worst["product_key"] == "b"

    def test_faller_only(self):
        forecastable = [
            {"product_key": "a", "title": "A", "delta_pct": -2.0},
            {"product_key": "b", "title": "B", "delta_pct": -10.0},
        ]
        riser, faller, best, worst = _pick_riser_faller(forecastable)
        assert riser is None
        assert faller == {"product_key": "b", "title": "B", "delta_pct": -10.0}
        assert best["product_key"] == "a"
        assert worst["product_key"] == "b"

    def test_both_riser_and_faller(self):
        forecastable = [
            {"product_key": "a", "title": "A", "delta_pct": 15.0},
            {"product_key": "b", "title": "B", "delta_pct": 0.0},
            {"product_key": "c", "title": "C", "delta_pct": -12.0},
        ]
        riser, faller, best, worst = _pick_riser_faller(forecastable)
        assert riser["product_key"] == "a"
        assert faller["product_key"] == "c"
        assert best["product_key"] == "a"
        assert worst["product_key"] == "c"

    def test_within_deadband_no_riser_no_faller(self):
        forecastable = [
            {"product_key": "a", "title": "A", "delta_pct": 3.0},
            {"product_key": "b", "title": "B", "delta_pct": -3.0},
        ]
        riser, faller, best, worst = _pick_riser_faller(forecastable)
        assert riser is None
        assert faller is None
        # best/worst still returned for downstream insight wording
        assert best["product_key"] == "a"
        assert worst["product_key"] == "b"


# ---------------------------------------------------------------------------
# _build_forecast_insight — 5-branch narrative
# ---------------------------------------------------------------------------


class TestBuildInsight:
    def test_zero_forecastable_cold_start_message(self):
        out = _build_forecast_insight(
            riser=None, faller=None, best=None, worst=None,
            horizon_days=14, top_n=10, forecastable_count=0,
        )
        assert "Need at least one product with 7+ days" in out

    def test_both_riser_and_faller(self):
        best = {"product_key": "a", "title": "Wallet", "delta_pct": 15.0}
        worst = {"product_key": "c", "title": "Bag", "delta_pct": -12.0}
        riser = {"product_key": "a", "title": "Wallet", "delta_pct": 15.0}
        faller = {"product_key": "c", "title": "Bag", "delta_pct": -12.0}
        out = _build_forecast_insight(
            riser=riser, faller=faller, best=best, worst=worst,
            horizon_days=14, top_n=10, forecastable_count=3,
        )
        assert "Wallet" in out and "15%" in out
        assert "Bag" in out and "12%" in out
        assert "Re-stock the riser" in out

    def test_riser_only(self):
        best = {"product_key": "a", "title": "Wallet", "delta_pct": 15.0}
        worst = {"product_key": "b", "title": "Other", "delta_pct": 2.0}
        riser = {"product_key": "a", "title": "Wallet", "delta_pct": 15.0}
        out = _build_forecast_insight(
            riser=riser, faller=None, best=best, worst=worst,
            horizon_days=14, top_n=10, forecastable_count=2,
        )
        assert "Wallet" in out and "rising 15%" in out
        assert "strongest" in out

    def test_faller_only(self):
        best = {"product_key": "a", "title": "Other", "delta_pct": 2.0}
        worst = {"product_key": "b", "title": "Bag", "delta_pct": -12.0}
        faller = {"product_key": "b", "title": "Bag", "delta_pct": -12.0}
        out = _build_forecast_insight(
            riser=None, faller=faller, best=best, worst=worst,
            horizon_days=14, top_n=10, forecastable_count=2,
        )
        assert "Bag" in out and "12%" in out
        assert "investigate" in out.lower()

    def test_all_stable(self):
        best = {"product_key": "a", "title": "A", "delta_pct": 3.0}
        worst = {"product_key": "b", "title": "B", "delta_pct": -3.0}
        out = _build_forecast_insight(
            riser=None, faller=None, best=best, worst=worst,
            horizon_days=14, top_n=10, forecastable_count=2,
        )
        assert "stable forecasts" in out
        assert "±5%" in out
        # top_n appears as "top-2" (forecastable_count)
        assert "top-2" in out

    def test_riser_and_faller_same_product_falls_through_to_riser(self):
        """If best and worst share product_key (only 1 forecastable),
        the 'both' branch is gated; the single-direction branch wins."""
        best = {"product_key": "a", "title": "X", "delta_pct": 15.0}
        riser = {"product_key": "a", "title": "X", "delta_pct": 15.0}
        # Same product as worst → the "both" branch gates on key inequality
        out = _build_forecast_insight(
            riser=riser, faller=None, best=best, worst=best,
            horizon_days=14, top_n=10, forecastable_count=1,
        )
        # Riser-only narrative — not the both-branch
        assert "Re-stock the riser" not in out
        assert "strongest" in out
