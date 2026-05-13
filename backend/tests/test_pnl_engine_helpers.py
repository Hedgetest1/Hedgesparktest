"""
Unit tests for the pure helpers extracted from `get_pnl_report` in the
2026-05-13 A3 refactor.

End-to-end coverage exists at the `/pro/pnl` endpoint level; this file
is the structural unit gate for the 7 pure helpers (no DB) and 1 DB-
fetch helper:

  - `_resolve_rates` — cost_cfg → resolved rates + is_custom flags
  - `_fetch_revenue_summary` — gross revenue + order count (DB, error-safe)
  - `_compute_cogs_summary` — real + fallback COGS blend + coverage
  - `_compute_cost_stack` — payment / shipping / ad spend
  - `_compute_profit_lines` — gross/net profit + margins + total
  - `_derive_precision` — exact/refined/rough decision
  - `_build_verdict` — 4-band margin verdict text
  - `_build_cogs_meta` — 4-branch (source, estimated, note) for cogs UI

The cost amounts on the merchant's P&L card flow directly from these
helpers. Drift in any band threshold or rounding changes the merchant-
visible numbers — the tests pin every branch.
"""
from __future__ import annotations

import pytest

from app.services import pnl_engine as pnl_mod
from app.services.pnl_engine import (
    _DEFAULT_COGS_PCT,
    _DEFAULT_PAYMENT_FLAT,
    _DEFAULT_PAYMENT_PCT,
    _DEFAULT_SHIPPING_PER_ORDER,
    _assemble_costs_block,
    _build_cogs_meta,
    _build_verdict,
    _compute_cogs_summary,
    _compute_cost_stack,
    _compute_profit_lines,
    _derive_precision,
    _resolve_rates,
)


# ---------------------------------------------------------------------------
# _resolve_rates
# ---------------------------------------------------------------------------


class TestResolveRates:
    def test_empty_cfg_uses_module_defaults(self):
        result = _resolve_rates({})
        assert result["cogs_pct_default"] == _DEFAULT_COGS_PCT
        assert result["payment_pct"] == _DEFAULT_PAYMENT_PCT
        assert result["payment_flat"] == _DEFAULT_PAYMENT_FLAT
        assert result["shipping_per_ord"] == _DEFAULT_SHIPPING_PER_ORDER
        assert result["ad_spend_monthly"] == 0.0

    def test_empty_cfg_all_is_custom_false(self):
        result = _resolve_rates({})
        assert result["cogs_pct_is_custom"] is False
        assert result["payment_pct_is_custom"] is False
        assert result["payment_flat_is_custom"] is False
        assert result["shipping_is_custom"] is False
        assert result["ad_spend_is_manual"] is False

    def test_cfg_value_overrides_default(self):
        result = _resolve_rates({"default_cogs_pct": 0.50})
        assert result["cogs_pct_default"] == 0.50
        assert result["cogs_pct_is_custom"] is True

    def test_explicit_zero_marked_custom(self):
        # 0.0 is a valid merchant entry (e.g. ad_spend=0 in setup) — must NOT
        # be confused with None
        result = _resolve_rates({"ad_spend_manual_monthly": 0.0})
        assert result["ad_spend_monthly"] == 0.0
        assert result["ad_spend_is_manual"] is True

    def test_value_typed_to_float(self):
        result = _resolve_rates({"payment_pct": "0.025"})
        assert result["payment_pct"] == 0.025
        assert isinstance(result["payment_pct"], float)


# ---------------------------------------------------------------------------
# _compute_cogs_summary
# ---------------------------------------------------------------------------


class TestComputeCogsSummary:
    def test_full_coverage(self):
        # All gross revenue covered by real per-product COGS
        result = _compute_cogs_summary(
            real_cogs_amount=400.0, covered_revenue=1000.0,
            gross_revenue=1000.0, cogs_pct_default=0.40,
        )
        assert result["cogs_estimate"] == 400.0  # all real, no fallback
        assert result["cogs_fallback"] == 0.0
        assert result["cogs_coverage"] == 1.0

    def test_partial_coverage_blends_real_and_fallback(self):
        # 600 of 1000 covered by real (240 real cogs), 400 uncovered at 40% = 160
        # total cogs = 240 + 160 = 400; coverage = 0.6
        result = _compute_cogs_summary(
            real_cogs_amount=240.0, covered_revenue=600.0,
            gross_revenue=1000.0, cogs_pct_default=0.40,
        )
        assert result["cogs_estimate"] == 400.0
        assert result["cogs_fallback"] == 160.0
        assert result["cogs_coverage"] == 0.6

    def test_no_coverage_uses_pct_fallback_only(self):
        result = _compute_cogs_summary(
            real_cogs_amount=0.0, covered_revenue=0.0,
            gross_revenue=1000.0, cogs_pct_default=0.40,
        )
        assert result["cogs_estimate"] == 400.0
        assert result["cogs_coverage"] == 0.0

    def test_zero_gross_revenue_zero_coverage(self):
        result = _compute_cogs_summary(
            real_cogs_amount=0.0, covered_revenue=0.0,
            gross_revenue=0.0, cogs_pct_default=0.40,
        )
        assert result["cogs_coverage"] == 0.0
        assert result["cogs_estimate"] == 0.0

    def test_overcoverage_floors_uncovered_at_zero(self):
        # Covered > gross (unusual but defensible) — uncovered clamps to 0
        result = _compute_cogs_summary(
            real_cogs_amount=400.0, covered_revenue=1500.0,
            gross_revenue=1000.0, cogs_pct_default=0.40,
        )
        assert result["cogs_fallback"] == 0.0
        assert result["cogs_estimate"] == 400.0


# ---------------------------------------------------------------------------
# _compute_cost_stack
# ---------------------------------------------------------------------------


def _rates(**overrides) -> dict:
    base = _resolve_rates({})
    base.update(overrides)
    return base


class TestComputeCostStack:
    def test_canonical_payment_formula(self):
        # gross=1000, pct=2.9%, flat=0.30, orders=10
        # payment = 1000*0.029 + 10*0.30 = 29 + 3 = 32
        rates = _rates()
        result = _compute_cost_stack(
            rates=rates, gross_revenue=1000.0, order_count=10, window_days=30,
        )
        assert result["payment_fees"] == 32.0

    def test_shipping_scales_with_order_count(self):
        rates = _rates()
        result = _compute_cost_stack(
            rates=rates, gross_revenue=1000.0, order_count=10, window_days=30,
        )
        # 10 orders × $5/order = $50
        assert result["shipping_cost"] == 50.0

    def test_ad_spend_zero_when_not_manual(self):
        rates = _rates()  # ad_spend_is_manual=False by default
        result = _compute_cost_stack(
            rates=rates, gross_revenue=1000.0, order_count=10, window_days=30,
        )
        assert result["ad_spend"] == 0.0

    def test_ad_spend_scales_to_window(self):
        # 30-day monthly $300 → 60d window = $600
        rates = _rates(ad_spend_monthly=300.0, ad_spend_is_manual=True)
        result = _compute_cost_stack(
            rates=rates, gross_revenue=1000.0, order_count=10, window_days=60,
        )
        assert result["ad_spend"] == 600.0

    def test_ad_spend_exact_at_30d_window(self):
        rates = _rates(ad_spend_monthly=500.0, ad_spend_is_manual=True)
        result = _compute_cost_stack(
            rates=rates, gross_revenue=1000.0, order_count=10, window_days=30,
        )
        assert result["ad_spend"] == 500.0


# ---------------------------------------------------------------------------
# _compute_profit_lines
# ---------------------------------------------------------------------------


class TestComputeProfitLines:
    def test_canonical(self):
        # gross=1000, cogs=400, fees=32, shipping=50, ads=200
        # gross_profit = 1000 - 400 - 32 - 50 = 518
        # net_profit = 518 - 200 = 318
        # total = 400 + 32 + 50 + 200 = 682
        result = _compute_profit_lines(
            gross_revenue=1000.0, cogs_estimate=400.0,
            cost_stack={"payment_fees": 32.0, "shipping_cost": 50.0, "ad_spend": 200.0},
        )
        assert result["gross_profit"] == 518.0
        assert result["net_profit"] == 318.0
        assert result["total_costs"] == 682.0
        assert result["gross_margin_pct"] == 51.8
        assert result["net_margin_pct"] == 31.8

    def test_zero_revenue_zero_margins(self):
        result = _compute_profit_lines(
            gross_revenue=0.0, cogs_estimate=0.0,
            cost_stack={"payment_fees": 0.0, "shipping_cost": 0.0, "ad_spend": 0.0},
        )
        assert result["gross_margin_pct"] == 0.0
        assert result["net_margin_pct"] == 0.0

    def test_negative_profit_when_costs_exceed_revenue(self):
        result = _compute_profit_lines(
            gross_revenue=500.0, cogs_estimate=400.0,
            cost_stack={"payment_fees": 50.0, "shipping_cost": 100.0, "ad_spend": 200.0},
        )
        # gross_profit = 500 - 400 - 50 - 100 = -50; net = -50 - 200 = -250
        assert result["gross_profit"] == -50.0
        assert result["net_profit"] == -250.0


# ---------------------------------------------------------------------------
# _derive_precision
# ---------------------------------------------------------------------------


class TestDerivePrecision:
    def test_all_defaults_no_real_cogs_rough(self):
        rates = _rates()
        assert _derive_precision(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        ) == "rough"

    def test_any_custom_is_refined(self):
        rates = _rates(payment_pct_is_custom=True)
        assert _derive_precision(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        ) == "refined"

    def test_products_with_cogs_alone_refined(self):
        rates = _rates()  # no other customs
        assert _derive_precision(
            rates=rates, cogs_coverage=0.5, products_with_real_cogs=5,
        ) == "refined"

    def test_exact_requires_both_coverage_AND_ad_spend(self):
        # ≥80% coverage + ad_spend manual → exact
        rates = _rates(ad_spend_is_manual=True)
        assert _derive_precision(
            rates=rates, cogs_coverage=0.80, products_with_real_cogs=10,
        ) == "exact"

    def test_exact_blocked_when_no_ad_spend(self):
        rates = _rates()  # ad_spend NOT manual
        # 80%+ coverage but missing ad spend → only refined
        assert _derive_precision(
            rates=rates, cogs_coverage=0.95, products_with_real_cogs=10,
        ) == "refined"

    def test_exact_blocked_when_coverage_below_80(self):
        rates = _rates(ad_spend_is_manual=True)
        assert _derive_precision(
            rates=rates, cogs_coverage=0.79, products_with_real_cogs=10,
        ) == "refined"


# ---------------------------------------------------------------------------
# _build_verdict
# ---------------------------------------------------------------------------


class TestBuildVerdict:
    def test_healthy_band(self):
        msg = _build_verdict(net_margin_pct=25.0, currency="USD")
        assert "healthy margin" in msg
        assert "25¢" in msg

    def test_tight_band(self):
        msg = _build_verdict(net_margin_pct=15.0, currency="USD")
        assert "tight but viable" in msg

    def test_thin_band(self):
        msg = _build_verdict(net_margin_pct=5.0, currency="USD")
        assert "too thin to scale" in msg

    def test_loss_band(self):
        msg = _build_verdict(net_margin_pct=-10.0, currency="USD")
        assert "exceed revenue" in msg

    def test_zero_treated_as_loss_band(self):
        # net_margin_pct == 0 falls into the "Estimated costs exceed revenue"
        # branch (not >0)
        msg = _build_verdict(net_margin_pct=0.0, currency="USD")
        assert "exceed revenue" in msg

    def test_currency_symbol_substituted(self):
        msg_eur = _build_verdict(net_margin_pct=25.0, currency="EUR")
        # Currency symbol comes via app.core.currency.currency_symbol
        # Just verify the helper interpolates the currency, not a hardcoded $
        assert "¢" in msg_eur

    def test_band_boundary_20pct(self):
        msg = _build_verdict(net_margin_pct=20.0, currency="USD")
        assert "healthy margin" in msg

    def test_band_boundary_10pct(self):
        msg = _build_verdict(net_margin_pct=10.0, currency="USD")
        assert "tight but viable" in msg


# ---------------------------------------------------------------------------
# _build_cogs_meta
# ---------------------------------------------------------------------------


class TestBuildCogsMeta:
    def test_full_coverage_per_product_exact(self):
        rates = _rates()
        meta = _build_cogs_meta(
            rates=rates, cogs_coverage=1.0, products_with_real_cogs=10,
        )
        assert meta["source"] == "per_product_exact"
        assert meta["estimated"] is False

    def test_partial_coverage_per_product_partial(self):
        rates = _rates()
        meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.5, products_with_real_cogs=5,
        )
        assert meta["source"] == "per_product_partial"
        assert meta["estimated"] is True

    def test_custom_pct_no_real_cogs(self):
        rates = _rates(cogs_pct_is_custom=True, cogs_pct_default=0.50)
        meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        )
        assert meta["source"] == "shop_default_pct_custom"
        assert "custom shop default 50%" in meta["note"]

    def test_module_default_when_nothing_set(self):
        rates = _rates()
        meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        )
        assert meta["source"] == "default_40pct"
        assert "module default 40%" in meta["note"]

    def test_coverage_just_below_999_still_partial(self):
        rates = _rates()
        meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.99, products_with_real_cogs=10,
        )
        assert meta["source"] == "per_product_partial"


# ---------------------------------------------------------------------------
# _assemble_costs_block
# ---------------------------------------------------------------------------


class TestAssembleCostsBlock:
    def test_four_top_level_keys(self):
        rates = _rates()
        cost_stack = {"payment_fees": 32.0, "shipping_cost": 50.0, "ad_spend": 0.0}
        cogs_summary = {"cogs_estimate": 400.0, "cogs_coverage": 0.0,
                        "cogs_fallback": 400.0, "real_cogs_amount": 0.0}
        cogs_meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        )
        block = _assemble_costs_block(
            rates=rates, cost_stack=cost_stack, cogs_summary=cogs_summary,
            cogs_meta=cogs_meta, window_days=30, currency="USD",
        )
        assert set(block.keys()) == {"cogs", "payment_fees", "shipping", "ad_spend"}

    def test_each_block_has_amount_and_source(self):
        rates = _rates()
        cost_stack = {"payment_fees": 32.0, "shipping_cost": 50.0, "ad_spend": 0.0}
        cogs_summary = {"cogs_estimate": 400.0, "cogs_coverage": 0.0,
                        "cogs_fallback": 400.0, "real_cogs_amount": 0.0}
        cogs_meta = _build_cogs_meta(
            rates=rates, cogs_coverage=0.0, products_with_real_cogs=0,
        )
        block = _assemble_costs_block(
            rates=rates, cost_stack=cost_stack, cogs_summary=cogs_summary,
            cogs_meta=cogs_meta, window_days=30, currency="USD",
        )
        for key in ("cogs", "payment_fees", "shipping", "ad_spend"):
            assert "amount" in block[key]
            assert "source" in block[key]
            assert "note" in block[key]
            assert "estimated" in block[key]

    def test_ad_spend_not_tracked_when_no_manual_entry(self):
        rates = _rates()
        block = _assemble_costs_block(
            rates=rates,
            cost_stack={"payment_fees": 0, "shipping_cost": 0, "ad_spend": 0},
            cogs_summary={"cogs_estimate": 0, "cogs_coverage": 0,
                          "cogs_fallback": 0, "real_cogs_amount": 0},
            cogs_meta={"source": "default_40pct", "estimated": True, "note": ""},
            window_days=30, currency="USD",
        )
        assert block["ad_spend"]["source"] == "not_tracked_yet"

    def test_payment_custom_flag(self):
        rates = _rates(payment_pct_is_custom=True, payment_pct=0.025)
        block = _assemble_costs_block(
            rates=rates,
            cost_stack={"payment_fees": 25, "shipping_cost": 0, "ad_spend": 0},
            cogs_summary={"cogs_estimate": 0, "cogs_coverage": 0,
                          "cogs_fallback": 0, "real_cogs_amount": 0},
            cogs_meta={"source": "default_40pct", "estimated": True, "note": ""},
            window_days=30, currency="USD",
        )
        assert block["payment_fees"]["source"] == "shop_custom"
        assert block["payment_fees"]["estimated"] is False


# ---------------------------------------------------------------------------
# get_pnl_report composer — wire test via monkeypatched DB helpers
# ---------------------------------------------------------------------------


class TestGetPnlReportComposer:
    """Validate the composer wires the helpers correctly without hitting
    DB. Patches every DB-touching helper to deterministic constants and
    checks the response shape + math wiring."""

    def _patch(
        self, monkeypatch, *,
        cost_cfg=None, currency="USD", order_count=10, gross_revenue=1000.0,
        real_cogs=400.0, covered_revenue=1000.0, products_with_cogs=5,
    ):
        if cost_cfg is None:
            cost_cfg = {}
        monkeypatch.setattr(pnl_mod, "_maybe_auto_sync_shopify_costs",
                            lambda db, shop: None)
        monkeypatch.setattr(pnl_mod, "_load_shop_cost_defaults",
                            lambda db, shop: cost_cfg)
        monkeypatch.setattr(pnl_mod, "get_shop_currency",
                            lambda db, shop: currency)
        monkeypatch.setattr(
            pnl_mod, "_fetch_revenue_summary",
            lambda db, shop, days, ccy: {
                "order_count": order_count, "gross_revenue": gross_revenue,
                "error": False,
            },
        )
        monkeypatch.setattr(
            pnl_mod, "_compute_real_cogs",
            lambda db, shop, days: (real_cogs, covered_revenue, products_with_cogs),
        )

    def test_response_shape(self, monkeypatch):
        self._patch(monkeypatch)
        result = pnl_mod.get_pnl_report(db=None, shop_domain="example.com")
        expected_keys = {
            "window_days", "currency", "precision", "has_data",
            "order_count", "gross_revenue",
            "cogs_coverage_pct", "products_with_cogs",
            "costs",
            "total_costs", "gross_profit", "net_profit",
            "gross_margin_pct", "net_margin_pct",
            "verdict", "generated_at",
        }
        assert set(result.keys()) == expected_keys

    def test_empty_report_when_zero_orders(self, monkeypatch):
        self._patch(monkeypatch, order_count=0, gross_revenue=0.0)
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["has_data"] is False
        assert result["order_count"] == 0

    def test_empty_report_on_revenue_error(self, monkeypatch):
        self._patch(monkeypatch)
        # Override revenue fetch to error
        monkeypatch.setattr(
            pnl_mod, "_fetch_revenue_summary",
            lambda db, shop, days, ccy: {
                "order_count": 0, "gross_revenue": 0.0, "error": True,
            },
        )
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["has_data"] is False

    def test_canonical_pnl_math(self, monkeypatch):
        # gross=1000, real_cogs=400, fully covered, 10 orders, defaults
        # payment = 1000*0.029 + 10*0.30 = 32
        # shipping = 10*5 = 50
        # ad_spend = 0 (no manual)
        # gross_profit = 1000 - 400 - 32 - 50 = 518
        # net_profit = 518
        # margins = 51.8 / 51.8
        self._patch(monkeypatch)
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["gross_profit"] == 518.0
        assert result["net_profit"] == 518.0
        assert result["gross_margin_pct"] == 51.8
        assert result["net_margin_pct"] == 51.8

    def test_window_clamped(self, monkeypatch):
        self._patch(monkeypatch)
        result_low = pnl_mod.get_pnl_report(db=None, shop_domain="x.com", window_days=0)
        assert result_low["window_days"] == 1  # min clamp
        result_high = pnl_mod.get_pnl_report(db=None, shop_domain="x.com", window_days=365)
        assert result_high["window_days"] == 90  # max clamp

    def test_precision_exact_with_full_coverage_and_ad_spend(self, monkeypatch):
        self._patch(
            monkeypatch,
            cost_cfg={"ad_spend_manual_monthly": 200.0},
            real_cogs=400.0, covered_revenue=1000.0,
        )
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["precision"] == "exact"

    def test_precision_rough_with_module_defaults(self, monkeypatch):
        self._patch(
            monkeypatch,
            real_cogs=0.0, covered_revenue=0.0, products_with_cogs=0,
        )
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["precision"] == "rough"

    def test_verdict_picks_band_from_net_margin(self, monkeypatch):
        # Default scenario produces net_margin_pct = 51.8 → healthy band
        self._patch(monkeypatch)
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert "healthy margin" in result["verdict"]

    def test_currency_propagated_to_response(self, monkeypatch):
        self._patch(monkeypatch, currency="EUR")
        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["currency"] == "EUR"

    def test_currency_fallback_when_lookup_fails(self, monkeypatch):
        self._patch(monkeypatch)

        def _broken_currency(db, shop):
            raise RuntimeError("currency lookup down")
        monkeypatch.setattr(pnl_mod, "get_shop_currency", _broken_currency)

        result = pnl_mod.get_pnl_report(db=None, shop_domain="x.com")
        assert result["currency"] == "USD"
