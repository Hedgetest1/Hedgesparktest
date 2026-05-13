"""
Unit tests for the pure helpers extracted from `compute_price_sensitivity`
in the 2026-05-13 A3 refactor.

This is the first test coverage for price_sensitivity.py (the module
had 0 dedicated tests prior to the refactor). The composer is locked
by test_price_sensitivity_composer.py.
"""
from __future__ import annotations

from app.services.price_sensitivity import (
    _band_label,
    _build_band_buckets,
    _build_product_barrier_record,
    _build_product_prices,
    _build_sensitivity_headline,
    _cache_key_for,
    _classify_band,
    _compute_band_summaries,
    _compute_interest_score,
    _empty_sensitivity_response,
    _humanize_url,
    _PRICE_BANDS,
)


# ---------------------------------------------------------------------------
# _band_label — currency-aware bucket label
# ---------------------------------------------------------------------------


class TestBandLabel:
    def test_finite_range_format(self):
        assert _band_label(15, 30, "USD") == "$15-30"

    def test_unbounded_high_uses_plus(self):
        assert _band_label(250, 99999, "USD") == "$250+"

    def test_eur_symbol(self):
        out = _band_label(15, 30, "EUR")
        assert "15-30" in out
        assert "€" in out

    def test_none_currency_falls_back_gracefully(self):
        # currency_symbol returns a fallback for None
        out = _band_label(15, 30, None)
        assert "15-30" in out


# ---------------------------------------------------------------------------
# _cache_key_for
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_starts_with_prefix(self):
        assert _cache_key_for("x.myshopify.com").startswith("hs:pricesens:v1:")

    def test_deterministic(self):
        assert _cache_key_for("a") == _cache_key_for("a")

    def test_different_shops_different_keys(self):
        assert _cache_key_for("a") != _cache_key_for("b")


# ---------------------------------------------------------------------------
# _build_product_prices — line_items → {url: price}
# ---------------------------------------------------------------------------


class TestBuildProductPrices:
    def test_single_item_extracted(self):
        rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        out = _build_product_prices(rows)
        assert out == {"/products/wallet": 29.99}

    def test_skips_non_list_payload(self):
        rows = [("not_a_list",), ({"a": 1},)]
        out = _build_product_prices(rows)
        assert out == {}

    def test_skips_non_dict_item(self):
        rows = [(["string_item", 42, {"product_handle": "x", "price": "10"}],)]
        out = _build_product_prices(rows)
        assert out == {"/products/x": 10.0}

    def test_skips_missing_handle(self):
        rows = [([{"price": "10"}],)]
        out = _build_product_prices(rows)
        assert out == {}

    def test_skips_zero_or_negative_price(self):
        rows = [([
            {"product_handle": "free", "price": "0"},
            {"product_handle": "negative", "price": "-5"},
            {"product_handle": "paid", "price": "10"},
        ],)]
        out = _build_product_prices(rows)
        assert "/products/free" not in out
        assert "/products/negative" not in out
        assert out["/products/paid"] == 10.0

    def test_latest_price_wins_when_repeated(self):
        rows = [
            ([{"product_handle": "x", "price": "10"}],),
            ([{"product_handle": "x", "price": "15"}],),
        ]
        out = _build_product_prices(rows)
        assert out["/products/x"] == 15.0  # later overrides


# ---------------------------------------------------------------------------
# _empty_sensitivity_response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    def test_shape(self):
        from datetime import datetime
        out = _empty_sensitivity_response("x.myshopify.com", "EUR", datetime(2025, 1, 1))
        assert out["bands"] == []
        assert out["products"] == []
        assert "Insufficient" in out["headline"]
        assert out["currency"] == "EUR"
        assert out["shop_domain"] == "x.myshopify.com"


# ---------------------------------------------------------------------------
# _build_band_buckets
# ---------------------------------------------------------------------------


class TestBuildBandBuckets:
    def test_one_bucket_per_band(self):
        bands_with_labels = [(0, 15, "$0-15"), (15, 30, "$15-30")]
        out = _build_band_buckets(bands_with_labels)
        assert set(out.keys()) == {"$0-15", "$15-30"}

    def test_bucket_has_zero_init_state(self):
        out = _build_band_buckets([(0, 15, "$0-15")])
        b = out["$0-15"]
        assert b["products"] == 0
        assert b["total_views"] == 0
        assert b["total_carts"] == 0
        assert b["total_purchases"] == 0
        assert b["total_dwell"] == 0.0
        assert b["dwell_samples"] == 0
        assert b["return_visitors"] == 0
        assert b["lo"] == 0
        assert b["hi"] == 15


# ---------------------------------------------------------------------------
# _classify_band — price → band label
# ---------------------------------------------------------------------------


class TestClassifyBand:
    def _bands(self):
        return [(lo, hi, _band_label(lo, hi, "USD")) for lo, hi in _PRICE_BANDS]

    def test_price_within_first_band(self):
        bands = self._bands()
        assert _classify_band(10.0, bands) == "$0-15"

    def test_price_at_band_boundary_goes_to_next(self):
        # Range is [lo, hi) — 15 belongs to $15-30
        bands = self._bands()
        assert _classify_band(15.0, bands) == "$15-30"

    def test_high_price_goes_to_unbounded_band(self):
        bands = self._bands()
        assert _classify_band(500.0, bands) == "$250+"

    def test_negative_price_returns_none(self):
        bands = self._bands()
        assert _classify_band(-1.0, bands) is None


# ---------------------------------------------------------------------------
# _compute_interest_score — 4-band behavioral signal
# ---------------------------------------------------------------------------


class TestInterestScore:
    def test_zero_when_all_below_thresholds(self):
        assert _compute_interest_score(dwell=5, scroll=20, return_visitors=0, unique_visitors=2) == 0

    def test_dwell_adds_30(self):
        assert _compute_interest_score(dwell=30, scroll=0, return_visitors=0, unique_visitors=0) == 30

    def test_scroll_adds_20(self):
        assert _compute_interest_score(dwell=0, scroll=60, return_visitors=0, unique_visitors=0) == 20

    def test_return_visitors_adds_30(self):
        assert _compute_interest_score(dwell=0, scroll=0, return_visitors=5, unique_visitors=0) == 30

    def test_unique_visitors_adds_20(self):
        assert _compute_interest_score(dwell=0, scroll=0, return_visitors=0, unique_visitors=10) == 20

    def test_all_present_sums_to_100(self):
        assert _compute_interest_score(dwell=30, scroll=60, return_visitors=5, unique_visitors=10) == 100

    def test_none_dwell_safe(self):
        assert _compute_interest_score(dwell=None, scroll=None, return_visitors=0, unique_visitors=0) == 0


# ---------------------------------------------------------------------------
# _build_product_barrier_record
# ---------------------------------------------------------------------------


class TestProductBarrierRecord:
    def test_record_shape(self):
        out = _build_product_barrier_record(
            purl="/products/wallet", price=29.99, views=10, carts=2,
            purchases=0, dwell=25.0, scroll=70.0, return_visitors=4,
            interest_score=80, purchase_score=0.0, gap=80,
        )
        assert out["product_url"] == "/products/wallet"
        assert out["product_name"] == "Wallet"
        assert out["price"] == 29.99
        assert out["views_7d"] == 10
        assert out["cvr_pct"] == 0.0
        assert out["cart_rate_pct"] == 20.0
        assert out["interest_score"] == 80
        assert out["price_barrier_gap"] == 80
        assert out["return_visitors"] == 4
        assert "above the willingness threshold" in out["signal"]

    def test_none_dwell_yields_none_field(self):
        out = _build_product_barrier_record(
            purl="/products/x", price=10, views=10, carts=0,
            purchases=0, dwell=None, scroll=None, return_visitors=0,
            interest_score=0, purchase_score=0.0, gap=0,
        )
        assert out["avg_dwell_sec"] is None
        assert out["avg_scroll_pct"] is None


# ---------------------------------------------------------------------------
# _compute_band_summaries
# ---------------------------------------------------------------------------


class TestBandSummaries:
    def test_empty_bands_filtered(self):
        band_stats = {
            "$0-15": {"products": 0, "total_views": 0, "total_carts": 0,
                      "total_purchases": 0, "total_dwell": 0.0,
                      "dwell_samples": 0, "return_visitors": 0,
                      "lo": 0, "hi": 15, "total_scroll": 0.0, "band": "$0-15"},
            "$15-30": {"products": 2, "total_views": 100, "total_carts": 30,
                       "total_purchases": 10, "total_dwell": 200.0,
                       "dwell_samples": 5, "return_visitors": 8,
                       "lo": 15, "hi": 30, "total_scroll": 0.0, "band": "$15-30"},
        }
        bwl = [(0, 15, "$0-15"), (15, 30, "$15-30")]
        out = _compute_band_summaries(band_stats, bwl)
        assert len(out) == 1
        assert out[0]["band"] == "$15-30"
        assert out[0]["cvr_pct"] == 10.0   # 10/100*100
        assert out[0]["cart_rate_pct"] == 30.0  # 30/100*100
        assert out[0]["avg_dwell_sec"] == 40.0  # 200/5

    def test_band_with_zero_views_yields_zero_rates(self):
        band_stats = {
            "$0-15": {"products": 1, "total_views": 0, "total_carts": 0,
                      "total_purchases": 0, "total_dwell": 0.0,
                      "dwell_samples": 0, "return_visitors": 0,
                      "lo": 0, "hi": 15, "total_scroll": 0.0, "band": "$0-15"},
        }
        out = _compute_band_summaries(band_stats, [(0, 15, "$0-15")])
        assert out[0]["cvr_pct"] == 0.0
        assert out[0]["cart_rate_pct"] == 0.0


# ---------------------------------------------------------------------------
# _build_sensitivity_headline
# ---------------------------------------------------------------------------


class TestHeadline:
    def test_no_bands_cold_start(self):
        out = _build_sensitivity_headline([], [])
        assert "Insufficient data" in out

    def test_sweet_spot_only_branch(self):
        # Only one populated band → "Best converting band" branch
        bands = [{"band": "$15-30", "cvr_pct": 12.5, "views": 100}]
        out = _build_sensitivity_headline(bands, [])
        assert "Best converting band" in out
        assert "$15-30" in out
        assert "12.5%" in out

    def test_sweet_spot_and_ceiling_branch(self):
        bands = [
            {"band": "$15-30", "cvr_pct": 12.5, "views": 100},
            {"band": "$50-100", "cvr_pct": 2.0, "views": 100},
        ]
        out = _build_sensitivity_headline(
            bands,
            [{"product_url": "/products/x"}],  # 1 barrier signal
        )
        assert "Sweet spot: $15-30" in out
        assert "Ceiling: $50-100" in out
        assert "1 products show price barrier" in out

    def test_ceiling_skipped_when_views_below_10(self):
        # Worst band has views < 10 → no ceiling, falls to single-band branch
        bands = [
            {"band": "$15-30", "cvr_pct": 12.5, "views": 100},
            {"band": "$50-100", "cvr_pct": 2.0, "views": 5},  # too few views
        ]
        out = _build_sensitivity_headline(bands, [])
        assert "Best converting band" in out
        assert "Ceiling" not in out
