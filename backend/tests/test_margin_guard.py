"""Tests for margin_guard (β3) — COGS-aware discount refusal."""
from __future__ import annotations

from unittest.mock import patch

from app.services.margin_guard import check_discount_safe


class TestDiscountChecks:
    def test_non_negative_always_safe(self, db):
        r = check_discount_safe(db, "any-shop.myshopify.com", 0)
        assert r.allowed is True
        assert r.reason == "non_negative_discount"

        r2 = check_discount_safe(db, "any-shop.myshopify.com", 5)
        assert r2.allowed is True

    def test_no_revenue_defers_to_contract(self, db):
        # No orders exist for this test shop → defer to contract bounds
        r = check_discount_safe(db, "margin-test-empty.myshopify.com", -10)
        assert r.allowed is True
        assert r.reason == "no_revenue_data_defer_to_contract"

    def test_deep_discount_blocked_with_revenue(self, db):
        # Mock the snapshot to have real revenue + 40% COGS
        with patch("app.services.margin_guard.get_margin_snapshot") as m:
            m.return_value = {
                "shop_domain": "x",
                "window_days": 30,
                "revenue_eur": 10000.0,
                "cogs_eur": 4000.0,  # 40% COGS
                "gross_margin_eur": 6000.0,
                "gross_margin_pct": 60.0,
                "cogs_pct_used": 40.0,
                "precision": "refined",
                "min_required_margin_pct": 20.0,
                "computed_at": "2026-04-12T00:00:00",
            }
            # -50% discount: new rev 5000, cogs 4000 → margin 20%
            # Right at the 20% floor — boundary case, should pass equality check
            r = check_discount_safe(db, "x", -50)
            assert r.allowed is True

            # -60% discount: new rev 4000, cogs 4000 → 0% margin
            r2 = check_discount_safe(db, "x", -60)
            assert r2.allowed is False
            assert "margin_floor_breach" in r2.reason
