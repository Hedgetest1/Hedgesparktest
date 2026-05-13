"""
Unit tests for the pure helpers extracted from `get_product_margin_drag`
in the 2026-05-13 A3 refactor.

End-to-end coverage exists via test_pnl_engine_helpers.py + test_margin_guard.py
(54 prior tests). This file locks the new structural-unit helpers:
threshold computation + per-product record builder + weighted-avg math +
drag computation.
"""
from __future__ import annotations

from app.services.pnl_engine import (
    _build_product_margin_record,
    _compute_revenue_threshold,
    _compute_total_margin_drag,
    _compute_weighted_avg_margin_pct,
    _DEFAULT_COGS_PCT,
)


# ---------------------------------------------------------------------------
# _compute_revenue_threshold
# ---------------------------------------------------------------------------


class TestRevenueThreshold:
    def test_minimum_1_euro(self):
        # 0 total revenue → max(1, min(100, 0)) = 1
        assert _compute_revenue_threshold(0.0) == 1.0

    def test_1pct_below_100(self):
        # 5000 → 1% = 50 → min(100, 50) = 50 → max(1, 50) = 50
        assert _compute_revenue_threshold(5000.0) == 50.0

    def test_capped_at_100(self):
        # 100000 → 1% = 1000 → min(100, 1000) = 100
        assert _compute_revenue_threshold(100_000.0) == 100.0

    def test_low_revenue_minimum_1(self):
        # 50 → 1% = 0.5 → min(100, 0.5) = 0.5 → max(1, 0.5) = 1.0
        assert _compute_revenue_threshold(50.0) == 1.0


# ---------------------------------------------------------------------------
# _build_product_margin_record
# ---------------------------------------------------------------------------


def _row(product_key="/p/wallet", title="Wallet", revenue=500.0,
         units_sold=10, cogs_per_unit=20.0, provenance="manual_entry"):
    return (product_key, title, revenue, units_sold, cogs_per_unit, provenance)


class TestProductMarginRecord:
    def test_below_threshold_returns_none(self):
        # rev=50, threshold=100 → filtered out
        out = _build_product_margin_record(
            _row(revenue=50.0), threshold=100.0,
        )
        assert out is None

    def test_full_record_with_real_cogs(self):
        # rev=500, units=10, cogs/unit=20 → cogs_total=200
        # margin_eur=300, margin_pct=60%
        out = _build_product_margin_record(_row(), threshold=10.0)
        assert out["product"] == "/p/wallet"
        assert out["title"] == "Wallet"
        assert out["revenue"] == 500.0
        assert out["cogs"] == 200.0
        assert out["cogs_source"] == "manual_entry"
        assert out["margin_eur"] == 300.0
        assert out["margin_pct"] == 60.0
        assert out["units_sold"] == 10

    def test_default_cogs_fallback_when_no_data(self):
        out = _build_product_margin_record(
            _row(cogs_per_unit=None), threshold=10.0,
        )
        # No real cogs → default 40% of revenue
        assert out["cogs"] == round(500.0 * _DEFAULT_COGS_PCT, 2)
        assert out["cogs_source"] == "default_40pct"

    def test_default_cogs_fallback_when_zero_units(self):
        out = _build_product_margin_record(
            _row(units_sold=0, cogs_per_unit=20.0), threshold=10.0,
        )
        # units=0 → can't multiply → fallback to default %
        assert out["cogs_source"] == "default_40pct"

    def test_title_fallback_to_product_key(self):
        out = _build_product_margin_record(
            _row(title=None), threshold=10.0,
        )
        assert out["title"] == "/p/wallet"

    def test_title_fallback_to_dash(self):
        out = _build_product_margin_record(
            _row(product_key=None, title=None), threshold=10.0,
        )
        assert out["title"] == "—"

    def test_provenance_fallback_to_manual_entry(self):
        out = _build_product_margin_record(
            _row(provenance=None), threshold=10.0,
        )
        # Real cogs present but no provenance → manual_entry default
        assert out["cogs_source"] == "manual_entry"


# ---------------------------------------------------------------------------
# _compute_weighted_avg_margin_pct
# ---------------------------------------------------------------------------


class TestWeightedAvgMargin:
    def test_empty_returns_zero(self):
        assert _compute_weighted_avg_margin_pct([]) == 0.0

    def test_single_product_passes_through(self):
        out = _compute_weighted_avg_margin_pct([
            {"revenue": 500.0, "margin_pct": 60.0},
        ])
        assert out == 60.0

    def test_revenue_weighted_average(self):
        # Two products: large+low-margin, small+high-margin → weight
        # toward the large one
        out = _compute_weighted_avg_margin_pct([
            {"revenue": 1000.0, "margin_pct": 30.0},  # weight 1000
            {"revenue": 100.0, "margin_pct": 80.0},   # weight 100
        ])
        # (30 * 1000 + 80 * 100) / 1100 = 34.5454...
        assert abs(out - (30 * 1000 + 80 * 100) / 1100) < 1e-9

    def test_zero_total_revenue_returns_zero(self):
        # All-zero revenue → defensive fallback
        out = _compute_weighted_avg_margin_pct([
            {"revenue": 0.0, "margin_pct": 50.0},
        ])
        assert out == 0.0


# ---------------------------------------------------------------------------
# _compute_total_margin_drag
# ---------------------------------------------------------------------------


class TestTotalMarginDrag:
    def test_empty_worst_yields_zero(self):
        assert _compute_total_margin_drag([], avg_margin_pct=50.0) == 0.0

    def test_above_avg_product_contributes_zero(self):
        # Product with HIGHER margin than avg should NOT be a drag
        worst = [{"revenue": 1000.0, "margin_pct": 80.0}]
        out = _compute_total_margin_drag(worst, avg_margin_pct=50.0)
        # delta_pct = max(0, 50 - 80) = 0 → drag = 0
        assert out == 0.0

    def test_below_avg_drags(self):
        # 1000 revenue at 30% margin vs 50% avg → delta=20pp → drag=200
        worst = [{"revenue": 1000.0, "margin_pct": 30.0}]
        out = _compute_total_margin_drag(worst, avg_margin_pct=50.0)
        assert out == 200.0

    def test_multi_product_drag_sums(self):
        worst = [
            {"revenue": 500.0, "margin_pct": 20.0},   # delta 30 → 150
            {"revenue": 300.0, "margin_pct": 35.0},   # delta 15 → 45
            {"revenue": 200.0, "margin_pct": 60.0},   # above avg → 0
        ]
        out = _compute_total_margin_drag(worst, avg_margin_pct=50.0)
        assert out == 195.0  # 150 + 45

    def test_negative_delta_clamped_to_zero(self):
        # A product above-avg must NOT subtract from drag
        worst = [
            {"revenue": 1000.0, "margin_pct": 10.0},  # +40pp = 400
            {"revenue": 1000.0, "margin_pct": 90.0},  # -40pp, clamped 0
        ]
        out = _compute_total_margin_drag(worst, avg_margin_pct=50.0)
        assert out == 400.0  # NOT 0 — the high-margin product is ignored
