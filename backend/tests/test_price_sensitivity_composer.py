"""
Composer-level integration tests for `compute_price_sensitivity`.

The 2026-05-13 A3 refactor decomposed the 210-LOC god function into
a 30-LOC composer + 13 pure helpers. test_price_sensitivity_helpers.py
(35 tests) locks every helper in isolation. This file locks the
composition: 5 IO seams + cache short-circuit + empty-state branch
+ band/product accumulation wiring.
"""
from __future__ import annotations

from app.services import price_sensitivity as ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_io(
    monkeypatch,
    *,
    price_rows=None,
    pm_rows=None,
    cache_hit=None,
    currency="USD",
):
    """Monkeypatch the 5 IO seams: cache load/save, price fetch,
    behavioral fetch, currency resolution."""
    saved: dict = {"set": None}
    monkeypatch.setattr(ps, "_load_cached_sensitivity", lambda s: cache_hit)
    monkeypatch.setattr(
        ps, "_save_cached_sensitivity",
        lambda s, r: saved.update({"set": r}),
    )
    monkeypatch.setattr(
        ps, "_fetch_price_rows",
        lambda db, s, cutoff: list(price_rows or []),
    )
    monkeypatch.setattr(
        ps, "_fetch_behavioral_rows",
        lambda db, s: list(pm_rows or []),
    )
    monkeypatch.setattr(ps, "_resolve_currency_sensitivity", lambda db, s: currency)
    return saved


# ---------------------------------------------------------------------------
# Cache short-circuit
# ---------------------------------------------------------------------------


class TestCacheShortCircuit:
    def test_cache_hit_returns_immediately(self, monkeypatch):
        cached = {"shop_domain": "x.myshopify.com", "bands": [], "products": []}

        def _explode(*_a, **_kw):
            raise AssertionError("MUST NOT run on cache hit")

        monkeypatch.setattr(ps, "_load_cached_sensitivity", lambda s: cached)
        monkeypatch.setattr(ps, "_fetch_price_rows", _explode)
        monkeypatch.setattr(ps, "_fetch_behavioral_rows", _explode)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out is cached


# ---------------------------------------------------------------------------
# Empty product_prices → empty-state branch
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_no_price_rows_returns_empty_response(self, monkeypatch):
        _patch_io(monkeypatch, price_rows=[], pm_rows=[], currency="EUR")
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out["bands"] == []
        assert out["products"] == []
        assert "Insufficient" in out["headline"]
        assert out["currency"] == "EUR"

    def test_empty_state_uses_usd_when_currency_resolution_returns_none(self, monkeypatch):
        _patch_io(monkeypatch, price_rows=[], pm_rows=[], currency=None)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "USD"

    def test_empty_state_skips_cache_write(self, monkeypatch):
        # Empty-state response is NOT cached (avoids pinning warming
        # period as "no data" for 6h).
        saved = _patch_io(monkeypatch, price_rows=[], pm_rows=[])
        ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert saved["set"] is None


# ---------------------------------------------------------------------------
# End-to-end with synthetic data
# ---------------------------------------------------------------------------


def _pm_row(url, views=10, carts=2, purchases=0, dwell=30.0,
            scroll=70.0, return_visitors=3, unique_visitors=10):
    return (url, views, carts, purchases, dwell, scroll,
            return_visitors, unique_visitors)


class TestEndToEnd:
    def test_single_product_barrier_surfaced(self, monkeypatch):
        # Price 29.99 → $15-30 band
        price_rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        pm_rows = [_pm_row("/products/wallet", views=20, purchases=0)]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert len(out["products"]) == 1
        p = out["products"][0]
        assert p["product_url"] == "/products/wallet"
        assert p["price"] == 29.99
        assert p["interest_score"] >= 50
        # cvr=0% + interest=100 → gap=100 → surfaces
        assert p["price_barrier_gap"] > 30

    def test_band_summary_aggregates_products(self, monkeypatch):
        price_rows = [
            ([{"product_handle": "p1", "price": "20"},
              {"product_handle": "p2", "price": "25"}],),
        ]
        pm_rows = [
            _pm_row("/products/p1", views=50, carts=10, purchases=2),
            _pm_row("/products/p2", views=50, carts=15, purchases=5),
        ]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        # Both products are in $15-30 band
        bands = [b for b in out["bands"] if b["band"] == "$15-30"]
        assert len(bands) == 1
        b = bands[0]
        assert b["products"] == 2
        assert b["views"] == 100
        assert b["carts"] == 25
        assert b["purchases"] == 7

    def test_no_pm_rows_yields_empty_bands_and_products(self, monkeypatch):
        # Prices exist, but no behavioral metrics → no products surface
        price_rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=[])
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out["products"] == []
        assert out["bands"] == []
        assert "Insufficient data" in out["headline"]

    def test_currency_propagates(self, monkeypatch):
        price_rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        pm_rows = [_pm_row("/products/wallet")]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows, currency="GBP")
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "GBP"

    def test_currency_defaults_to_usd(self, monkeypatch):
        price_rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        pm_rows = [_pm_row("/products/wallet")]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows, currency=None)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "USD"

    def test_product_cap_at_10(self, monkeypatch):
        # 15 products all with barrier signals → cap at 10
        price_rows = [([
            {"product_handle": f"p{i}", "price": "20"} for i in range(15)
        ],)]
        pm_rows = [
            _pm_row(f"/products/p{i}", views=20, purchases=0)
            for i in range(15)
        ]
        _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert len(out["products"]) == 10


# ---------------------------------------------------------------------------
# Cache write on success
# ---------------------------------------------------------------------------


class TestCacheWrite:
    def test_successful_compute_writes_cache(self, monkeypatch):
        price_rows = [([{"product_handle": "wallet", "price": "29.99"}],)]
        pm_rows = [_pm_row("/products/wallet")]
        saved = _patch_io(monkeypatch, price_rows=price_rows, pm_rows=pm_rows)
        out = ps.compute_price_sensitivity(db=None, shop_domain="x.myshopify.com")
        assert saved["set"] is not None
        # Cache stores the same payload returned to caller
        assert saved["set"] == out
