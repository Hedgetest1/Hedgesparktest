"""
Unit tests for the pure helpers extracted from `get_product_conversions`
in the 2026-05-13 A3 refactor.

This is the first test coverage for the orders.py product-conversions
endpoint. Locks the per-row math + empty-state response.
"""
from __future__ import annotations

from app.api.orders import (
    _build_empty_conversions_response,
    _build_product_conversion_record,
)


# ---------------------------------------------------------------------------
# _build_empty_conversions_response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    def test_shape(self):
        out = _build_empty_conversions_response(days=7, currency="USD")
        assert out == {
            "products": [], "days": 7, "currency": "USD", "has_data": False,
        }

    def test_currency_round_trip(self):
        out = _build_empty_conversions_response(days=30, currency="EUR")
        assert out["currency"] == "EUR"
        assert out["days"] == 30
        assert out["has_data"] is False


# ---------------------------------------------------------------------------
# _build_product_conversion_record — per-row math
# ---------------------------------------------------------------------------


def _row(url="/products/wallet", name="Wallet", total_views=100,
         view_visitors=50, atc_visitors=10, purchases=5, units_sold=8,
         revenue=499.99, converted_visitors=4):
    return (
        url, name, total_views, view_visitors, atc_visitors,
        purchases, units_sold, revenue, converted_visitors,
    )


class TestProductConversionRecord:
    def test_shape(self):
        out = _build_product_conversion_record(_row())
        assert set(out.keys()) == {
            "product_url", "product_name", "views", "unique_viewers",
            "add_to_cart", "purchases", "units_sold", "revenue",
            "cvr", "atc_rate", "avg_order_value",
        }

    def test_basic_math(self):
        out = _build_product_conversion_record(
            _row(view_visitors=100, atc_visitors=20, converted_visitors=10,
                 purchases=10, revenue=500.0),
        )
        # CVR uses converted_visitors / view_visitors, NOT orders / views
        assert out["cvr"] == 0.1  # 10/100
        assert out["atc_rate"] == 0.2  # 20/100
        assert out["avg_order_value"] == 50.0  # 500/10

    def test_zero_view_visitors_zero_rates(self):
        out = _build_product_conversion_record(
            _row(view_visitors=0, atc_visitors=0, converted_visitors=0),
        )
        assert out["cvr"] == 0.0
        assert out["atc_rate"] == 0.0

    def test_zero_purchases_zero_aov(self):
        out = _build_product_conversion_record(
            _row(purchases=0, revenue=0.0),
        )
        assert out["avg_order_value"] == 0.0

    def test_true_cvr_uses_converted_not_orders(self):
        # 20 orders but only 5 viewed-then-purchased — true CVR uses 5
        # (the rest are POS / external / cross-attributed sales)
        out = _build_product_conversion_record(
            _row(view_visitors=100, purchases=20, converted_visitors=5),
        )
        # CVR = 5/100 = 0.05 (NOT 20/100 = 0.20)
        assert out["cvr"] == 0.05

    def test_revenue_rounded_to_2dp(self):
        out = _build_product_conversion_record(_row(revenue=123.456789))
        assert out["revenue"] == 123.46

    def test_product_name_falls_back_to_url_when_null(self):
        out = _build_product_conversion_record(_row(name=None))
        assert out["product_name"] == "/products/wallet"

    def test_null_numeric_fields_treated_as_zero(self):
        # SQL can return None for null aggregates
        out = _build_product_conversion_record(
            ("/products/x", "X", None, None, None, None, None, None, None),
        )
        assert out["views"] == 0
        assert out["unique_viewers"] == 0
        assert out["add_to_cart"] == 0
        assert out["purchases"] == 0
        assert out["units_sold"] == 0
        assert out["revenue"] == 0.0
        assert out["cvr"] == 0.0

    def test_cvr_rounded_to_4dp(self):
        # 1/3 = 0.3333... → rounded to 0.3333
        out = _build_product_conversion_record(
            _row(view_visitors=3, converted_visitors=1),
        )
        assert out["cvr"] == 0.3333
