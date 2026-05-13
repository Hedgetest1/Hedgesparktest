"""
Composer-level integration tests for `forecast_by_sku`.

The 2026-05-13 A3 refactor decomposed the 286-LOC god function into
a composer + 8 pure helpers. test_forecast_by_sku_helpers.py (30
tests) locks every helper in isolation. test_forecast_by_sku.py (10
tests, prior) exercises end-to-end via Postgres. This file fills the
middle layer — composition + stage wiring tests that run hermetic
without booting Postgres.

Pattern: monkeypatch the 4 IO seams (top-products SQL, daily-series
SQL, currency, timezone), drive the composer with deterministic
inputs, assert orchestration + response shape + early-exit branches.
"""
from __future__ import annotations

from datetime import datetime

from app.services import probabilistic_forecast as pf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_daily(start_value: float, count: int, growth: float = 0.0) -> list:
    """Build deterministic daily rows: list of (date, revenue) tuples."""
    return [
        (f"2025-01-{i+1:02d}", start_value + i * growth)
        for i in range(count)
    ]


def _patch_io(monkeypatch, *, top_products, daily_by_pkey,
              currency="USD", tz="UTC"):
    """Wire 4 IO seams to deterministic returns."""
    monkeypatch.setattr(pf, "get_shop_currency", lambda db, s: currency)
    monkeypatch.setattr(pf, "get_shop_timezone", lambda db, s: tz)
    monkeypatch.setattr(
        pf, "_fetch_top_products",
        lambda db, s, since, ccy, top_n: list(top_products),
    )
    monkeypatch.setattr(
        pf, "_fetch_daily_series",
        lambda db, s, since, ccy, t, pkeys: dict(daily_by_pkey),
    )


# ---------------------------------------------------------------------------
# Empty-state branch
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_no_top_products_returns_empty_forecast(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={})
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["products"] == []
        assert out["biggest_riser"] is None
        assert out["biggest_faller"] is None
        assert "line-item data flows" in out["insight"]

    def test_empty_payload_top_level_keys(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={})
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert set(out.keys()) >= {
            "shop_domain", "horizon_days", "window_days", "currency",
            "generated_at", "products", "biggest_riser", "biggest_faller",
            "insight",
        }


# ---------------------------------------------------------------------------
# Param clamping flows through to response
# ---------------------------------------------------------------------------


class TestParamClampPropagation:
    def test_horizon_clamp_visible_in_response(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={})
        out = pf.forecast_by_sku(
            db=None, shop_domain="x.myshopify.com", horizon_days=999,
        )
        assert out["horizon_days"] == 60

    def test_window_clamp_visible_in_response(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={})
        out = pf.forecast_by_sku(
            db=None, shop_domain="x.myshopify.com", window_days=1,
        )
        assert out["window_days"] == 7


# ---------------------------------------------------------------------------
# Insufficient data per product
# ---------------------------------------------------------------------------


class TestInsufficientData:
    def test_product_below_min_points_marked_insufficient(self, monkeypatch):
        # 3 days < _MIN_POINTS_FOR_FORECAST=7
        top = [("p1", "Widget", 100.0)]
        daily = {"p1": _make_daily(10.0, 3)}
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert len(out["products"]) == 1
        p = out["products"][0]
        assert p["confidence"] == "insufficient"
        assert p["forecast_point"] == 0.0
        assert p["product_key"] == "p1"
        assert p["title"] == "Widget"
        assert p["observed_revenue"] == 100.0

    def test_no_forecastable_yields_cold_start_insight(self, monkeypatch):
        top = [("p1", "X", 10.0), ("p2", "Y", 20.0)]
        daily = {"p1": _make_daily(5.0, 3), "p2": _make_daily(5.0, 4)}
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert "Need at least one product with 7+" in out["insight"]
        assert out["biggest_riser"] is None
        assert out["biggest_faller"] is None


# ---------------------------------------------------------------------------
# Per-product forecast wiring
# ---------------------------------------------------------------------------


class TestProductForecastWiring:
    def test_seven_days_yields_forecastable_product(self, monkeypatch):
        # Flat 10/day for 14 days → forecast ≈ 10/day, direction stable
        top = [("p1", "Widget", 140.0)]
        daily = {"p1": _make_daily(10.0, 14)}
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        p = out["products"][0]
        assert p["confidence"] != "insufficient"
        assert p["n_days"] == 14
        # Flat series → direction stable (within ±5%)
        assert p["direction"] == "stable"
        assert p["forecast_point"] > 0

    def test_growing_series_classified_rising(self, monkeypatch):
        # 14 days, growing 1.0 → 14.0 → strong upward trend
        top = [("p1", "Widget", 100.0)]
        daily = {"p1": _make_daily(1.0, 14, growth=1.0)}
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        p = out["products"][0]
        assert p["direction"] == "rising"
        assert p["delta_pct"] > 5

    def test_observed_revenue_round_tripped(self, monkeypatch):
        top = [("p1", "Widget", 123.456)]
        daily = {"p1": _make_daily(10.0, 14)}
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["products"][0]["observed_revenue"] == 123.46

    def test_missing_daily_falls_to_insufficient(self, monkeypatch):
        """Top product with no daily series → insufficient (not crash)."""
        top = [("p1", "Widget", 100.0)]
        daily = {}  # pkey missing
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        p = out["products"][0]
        assert p["confidence"] == "insufficient"


# ---------------------------------------------------------------------------
# Biggest riser / faller selection
# ---------------------------------------------------------------------------


class TestRiserFallerSelection:
    def test_both_riser_and_faller_present(self, monkeypatch):
        top = [
            ("rise", "Rising", 1000.0),
            ("fall", "Falling", 800.0),
        ]
        daily = {
            "rise": _make_daily(1.0, 14, growth=2.0),    # strong up
            "fall": _make_daily(40.0, 14, growth=-3.0),  # strong down (40→13)
        }
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["biggest_riser"] is not None
        assert out["biggest_riser"]["product_key"] == "rise"
        assert out["biggest_faller"] is not None
        assert out["biggest_faller"]["product_key"] == "fall"
        # 2-product riser+faller branch — narrative
        assert "Re-stock the riser" in out["insight"]

    def test_riser_only(self, monkeypatch):
        top = [("rise", "Rising", 100.0), ("flat", "Flat", 100.0)]
        daily = {
            "rise": _make_daily(1.0, 14, growth=2.0),
            "flat": _make_daily(10.0, 14),
        }
        _patch_io(monkeypatch, top_products=top, daily_by_pkey=daily)
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["biggest_riser"] is not None
        assert out["biggest_faller"] is None
        assert "strongest" in out["insight"]


# ---------------------------------------------------------------------------
# Generated_at + currency end-to-end
# ---------------------------------------------------------------------------


class TestEndToEndShape:
    def test_currency_propagates_via_get_shop_currency(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={}, currency="EUR")
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "EUR"

    def test_currency_defaults_to_usd_when_none(self, monkeypatch):
        monkeypatch.setattr(pf, "get_shop_currency", lambda db, s: None)
        monkeypatch.setattr(pf, "get_shop_timezone", lambda db, s: "UTC")
        monkeypatch.setattr(pf, "_fetch_top_products",
                            lambda db, s, since, ccy, top_n: [])
        monkeypatch.setattr(pf, "_fetch_daily_series",
                            lambda db, s, since, ccy, t, pkeys: {})
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "USD"

    def test_generated_at_is_iso_with_z(self, monkeypatch):
        _patch_io(monkeypatch, top_products=[], daily_by_pkey={})
        out = pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert out["generated_at"].endswith("Z")
        # Strip the trailing Z and confirm parseable
        datetime.fromisoformat(out["generated_at"].rstrip("Z"))


# ---------------------------------------------------------------------------
# pkey extraction passed through to daily fetch
# ---------------------------------------------------------------------------


class TestPkeysPassedToDailyFetch:
    def test_pkeys_collected_from_top_products(self, monkeypatch):
        captured = {"pkeys": None}

        def _fake_daily(db, s, since, ccy, t, pkeys):
            captured["pkeys"] = list(pkeys)
            return {p: _make_daily(10.0, 14) for p in pkeys}

        monkeypatch.setattr(pf, "get_shop_currency", lambda db, s: "USD")
        monkeypatch.setattr(pf, "get_shop_timezone", lambda db, s: "UTC")
        monkeypatch.setattr(
            pf, "_fetch_top_products",
            lambda db, s, since, ccy, top_n: [
                ("a", "A", 100.0), ("b", "B", 50.0), ("c", "C", 25.0),
            ],
        )
        monkeypatch.setattr(pf, "_fetch_daily_series", _fake_daily)
        pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert captured["pkeys"] == ["a", "b", "c"]

    def test_none_pkeys_filtered_out(self, monkeypatch):
        captured = {"pkeys": None}

        def _fake_daily(db, s, since, ccy, t, pkeys):
            captured["pkeys"] = list(pkeys)
            return {}

        monkeypatch.setattr(pf, "get_shop_currency", lambda db, s: "USD")
        monkeypatch.setattr(pf, "get_shop_timezone", lambda db, s: "UTC")
        monkeypatch.setattr(
            pf, "_fetch_top_products",
            lambda db, s, since, ccy, top_n: [
                ("a", "A", 100.0), (None, "B", 50.0),  # None pkey
            ],
        )
        monkeypatch.setattr(pf, "_fetch_daily_series", _fake_daily)
        pf.forecast_by_sku(db=None, shop_domain="x.myshopify.com")
        assert captured["pkeys"] == ["a"]
