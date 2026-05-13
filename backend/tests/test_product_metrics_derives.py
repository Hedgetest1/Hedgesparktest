"""
Unit tests for the 5 pure helpers extracted from `compute_metrics` in
the 2026-05-12 A3 refactor (commit 047c7ce).

End-to-end coverage exists via the aggregation_worker integration
tests; this file is the structural unit gate for:
  - `_cutoffs` — SQL window timestamps
  - `_zero_metrics` — all-zero baseline shape
  - `_extract_base_counts` — row→typed-dict mapping
  - `_derive_traffic_breakdown` — paid/direct/organic residual math
  - `_derive_hourly_breakdown` — peak-block detection from JSON
  - `_derive_session_context` — landing-vs-browsing partition
"""
from __future__ import annotations

from app.workers.tasks.product_metrics_task import (
    _cutoffs,
    _derive_hourly_breakdown,
    _derive_session_context,
    _derive_traffic_breakdown,
    _extract_base_counts,
    _zero_metrics,
)


# ---------------------------------------------------------------------------
# _cutoffs — SQL window timestamps
# ---------------------------------------------------------------------------


class TestCutoffs:
    def test_canonical(self):
        now = 1_000_000_000
        cuts = _cutoffs(now)
        assert cuts["cutoff_1h"] == now - 3_600_000
        assert cuts["cutoff_24h"] == now - 86_400_000
        assert cuts["cutoff_7d"] == now - 604_800_000

    def test_keys_complete(self):
        cuts = _cutoffs(0)
        assert set(cuts.keys()) == {"cutoff_1h", "cutoff_24h", "cutoff_7d"}


# ---------------------------------------------------------------------------
# _zero_metrics — baseline shape
# ---------------------------------------------------------------------------


class TestZeroMetrics:
    def test_carries_shop_and_product(self):
        z = _zero_metrics("shop.example.com", "/products/x")
        assert z["shop_domain"] == "shop.example.com"
        assert z["product_url"] == "/products/x"

    def test_count_keys_are_zero(self):
        z = _zero_metrics("shop", "/products/x")
        zero_keys = [
            "views_1h", "views_24h", "views_7d",
            "unique_visitors_24h", "cart_conversions_24h",
            "return_visitor_count_7d",
            "views_mobile", "views_desktop",
            "carts_mobile", "carts_desktop",
            "views_paid", "views_organic", "views_direct",
            "peak_hour_views", "off_peak_hour_views",
            "landing_views_24h", "browsing_views_24h",
        ]
        for k in zero_keys:
            assert z[k] == 0

    def test_avg_keys_are_none(self):
        z = _zero_metrics("shop", "/products/x")
        assert z["avg_dwell_24h"] is None
        assert z["avg_scroll_24h"] is None
        assert z["last_event_at"] is None


# ---------------------------------------------------------------------------
# _extract_base_counts
# ---------------------------------------------------------------------------


def _row(**kwargs):
    """Build a dict shaped like the SQL row mapping passed in."""
    defaults = {
        "views_1h": 0, "views_24h": 0, "views_7d": 0,
        "unique_visitors_24h": 0, "unique_visitors_7d": 0,
        "cart_conversions_24h": 0, "cart_conversions_7d": 0,
        "return_visitor_count_7d": 0,
        "avg_dwell_24h": None, "avg_scroll_24h": None,
        "last_event_at": None,
        "views_mobile": 0, "views_desktop": 0,
        "carts_mobile": 0, "carts_desktop": 0,
    }
    defaults.update(kwargs)
    return defaults


class TestExtractBaseCounts:
    def test_canonical(self):
        row = _row(views_24h=100, cart_conversions_24h=5)
        result = _extract_base_counts(row)
        assert result["views_24h"] == 100
        assert result["cart_conversions_24h"] == 5

    def test_null_counts_become_zero(self):
        row = _row(views_24h=None)
        result = _extract_base_counts(row)
        assert result["views_24h"] == 0

    def test_avg_dwell_typed_to_float(self):
        row = _row(avg_dwell_24h=25.5)
        result = _extract_base_counts(row)
        assert result["avg_dwell_24h"] == 25.5
        assert isinstance(result["avg_dwell_24h"], float)

    def test_avg_dwell_none_preserved(self):
        row = _row(avg_dwell_24h=None)
        result = _extract_base_counts(row)
        assert result["avg_dwell_24h"] is None

    def test_last_event_at_int_typed(self):
        row = _row(last_event_at=1_000_000)
        result = _extract_base_counts(row)
        assert result["last_event_at"] == 1_000_000


# ---------------------------------------------------------------------------
# _derive_traffic_breakdown — residual organic math
# ---------------------------------------------------------------------------


class TestDeriveTrafficBreakdown:
    def test_organic_is_views_minus_paid_minus_direct(self):
        row = {"views_paid": 30, "views_direct": 20, "carts_paid": 0, "carts_direct": 0}
        result = _derive_traffic_breakdown(row, views_24h=100, cart_conversions_24h=0)
        assert result["views_organic"] == 50  # 100 - 30 - 20

    def test_organic_floored_at_zero(self):
        # paid + direct exceeds total → organic clamped to 0
        row = {"views_paid": 60, "views_direct": 50, "carts_paid": 0, "carts_direct": 0}
        result = _derive_traffic_breakdown(row, views_24h=100, cart_conversions_24h=0)
        assert result["views_organic"] == 0

    def test_carts_organic_floored_at_zero(self):
        row = {"views_paid": 0, "views_direct": 0, "carts_paid": 3, "carts_direct": 2}
        result = _derive_traffic_breakdown(row, views_24h=100, cart_conversions_24h=4)
        # 4 - 3 - 2 = -1 → clamped to 0
        assert result["carts_organic"] == 0

    def test_null_paid_becomes_zero(self):
        row = {"views_paid": None, "views_direct": 20, "carts_paid": None, "carts_direct": 0}
        result = _derive_traffic_breakdown(row, views_24h=100, cart_conversions_24h=0)
        assert result["views_paid"] == 0


# ---------------------------------------------------------------------------
# _derive_hourly_breakdown — peak detection
# ---------------------------------------------------------------------------


class TestDeriveHourlyBreakdown:
    def test_canonical_peak_block(self):
        blocks = [
            {"blk": "0", "v": 5, "c": 1},
            {"blk": "1", "v": 30, "c": 5},  # peak
            {"blk": "2", "v": 10, "c": 2},
        ]
        result = _derive_hourly_breakdown(blocks)
        assert result["peak_hour_views"] == 30
        assert result["peak_hour_carts"] == 5
        # Off-peak aggregates the other 2 blocks
        assert result["off_peak_hour_views"] == 15  # 5 + 10
        assert result["off_peak_hour_carts"] == 3   # 1 + 2

    def test_empty_returns_zeros(self):
        result = _derive_hourly_breakdown(None)
        assert result["peak_hour_views"] == 0
        assert result["peak_hour_carts"] == 0

    def test_empty_list(self):
        result = _derive_hourly_breakdown([])
        assert result["peak_hour_views"] == 0

    def test_json_string_parsed(self):
        import json
        json_str = json.dumps([{"blk": "0", "v": 20, "c": 4}])
        result = _derive_hourly_breakdown(json_str)
        assert result["peak_hour_views"] == 20

    def test_malformed_json_handled(self):
        # Should not raise — fall back to zeros
        result = _derive_hourly_breakdown("not a json")
        assert result["peak_hour_views"] == 0
        assert result["peak_hour_carts"] == 0


# ---------------------------------------------------------------------------
# _derive_session_context — landing vs browsing
# ---------------------------------------------------------------------------


class TestDeriveSessionContext:
    def test_browsing_is_total_minus_landing(self):
        row = {"landing_views_24h": 30, "landing_carts_24h": 2}
        result = _derive_session_context(row, views_24h=100, cart_conversions_24h=10)
        assert result["landing_views_24h"] == 30
        assert result["browsing_views_24h"] == 70
        assert result["landing_carts_24h"] == 2
        assert result["browsing_carts_24h"] == 8

    def test_browsing_floored_at_zero(self):
        row = {"landing_views_24h": 150, "landing_carts_24h": 0}
        result = _derive_session_context(row, views_24h=100, cart_conversions_24h=0)
        # 100 - 150 = -50 → clamped to 0
        assert result["browsing_views_24h"] == 0

    def test_null_landing_becomes_zero(self):
        row = {"landing_views_24h": None, "landing_carts_24h": None}
        result = _derive_session_context(row, views_24h=100, cart_conversions_24h=5)
        assert result["landing_views_24h"] == 0
        assert result["landing_carts_24h"] == 0
        assert result["browsing_views_24h"] == 100
        assert result["browsing_carts_24h"] == 5
