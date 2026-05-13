"""
Composer-level integration tests for `compute_abandoned_intent`.

The 2026-05-13 A3 refactor decomposed the 246-LOC god function into
a 25-LOC composer + 12 pure helpers. The 32 helper tests lock each
unit in isolation; the 10 prior tests in test_abandoned_intent.py
exercise end-to-end via Postgres. This file fills the middle: the
composer's IO wiring + cache short-circuit + plan filter, hermetic.
"""
from __future__ import annotations

from app.services import abandoned_intent as ai


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_io(monkeypatch, *, rows, cache_hit=None,
              currency="USD"):
    """Monkeypatch 4 IO seams: cache load/save + fetch_events + currency."""
    saved: dict = {"set": None}
    monkeypatch.setattr(ai, "_load_cached_intent", lambda s: cache_hit)
    monkeypatch.setattr(
        ai, "_save_cached_intent",
        lambda s, r: saved.update({"set": r}),
    )
    monkeypatch.setattr(ai, "_fetch_events", lambda db, s, cutoff: list(rows))
    monkeypatch.setattr(ai, "_resolve_currency", lambda db, s: currency)
    return saved


# ---------------------------------------------------------------------------
# Cache short-circuit
# ---------------------------------------------------------------------------


class TestCacheShortCircuit:
    def test_cache_hit_returns_cached_with_plan_filter(self, monkeypatch):
        cached = {
            "shop_domain": "x.myshopify.com",
            "products": [{"product_url": f"/p/{i}"} for i in range(10)],
            "total_products_count": 10,
            "session_insights": {"buyer_avg_events": 5.0},
            "headline": "cached",
            "currency": "USD",
            "generated_at": "2025-01-01T00:00:00",
        }

        def _explode(*_a, **_kw):
            raise AssertionError("_fetch_events MUST NOT run on cache hit")

        monkeypatch.setattr(ai, "_load_cached_intent", lambda s: cached)
        monkeypatch.setattr(ai, "_fetch_events", _explode)
        monkeypatch.setattr(ai, "_resolve_currency", lambda db, s: "USD")

        out_pro = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="pro")
        assert out_pro["products"] == cached["products"]  # full list
        assert out_pro["session_insights"] == cached["session_insights"]

        out_lite = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="lite")
        assert len(out_lite["products"]) == 3  # _LITE_PRODUCT_CAP
        assert out_lite["session_insights"] == {}  # redacted

    def test_cache_miss_runs_pipeline_and_writes(self, monkeypatch):
        # Non-empty rows so the pipeline runs through to cache write
        # (empty-rows path returns early and intentionally skips the
        # cache to avoid pinning false negatives).
        rows = _make_event_rows({
            f"v{i}": [("product_view", "/p/x", 1000 + i * 100)]
            for i in range(3)
        })
        saved = _patch_io(monkeypatch, rows=rows, cache_hit=None, currency="EUR")
        ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        assert saved["set"] is not None
        # Cache stores the full result (pre-plan-filter)
        assert saved["set"]["currency"] == "EUR"

    def test_empty_rows_skips_cache_write(self, monkeypatch):
        # Documents the intentional design: empty result is NOT cached
        # so a transient empty-data window doesn't pin a 3h "no data"
        # response on every subsequent caller.
        saved = _patch_io(monkeypatch, rows=[], cache_hit=None)
        ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        assert saved["set"] is None


# ---------------------------------------------------------------------------
# Empty events → empty response
# ---------------------------------------------------------------------------


class TestEmptyEvents:
    def test_no_events_returns_empty(self, monkeypatch):
        _patch_io(monkeypatch, rows=[], currency="EUR")
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        assert out["products"] == []
        assert out["total_products_count"] == 0
        assert "Insufficient data" in out["headline"]
        assert out["currency"] == "EUR"

    def test_empty_response_plan_filter_applies(self, monkeypatch):
        _patch_io(monkeypatch, rows=[], currency="EUR")
        # Lite: same empty result (no products to slice, insights already {})
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="lite")
        assert out["products"] == []
        assert out["session_insights"] == {}


# ---------------------------------------------------------------------------
# End-to-end with synthetic events
# ---------------------------------------------------------------------------


def _make_event_rows(events_per_visitor: dict[str, list]) -> list[tuple]:
    """Build SQL-shape rows: (visitor_id, event_type, product_url, timestamp)."""
    rows = []
    for vid, events in events_per_visitor.items():
        for et, url, ts in events:
            rows.append((vid, et, url, ts))
    rows.sort(key=lambda r: (r[0], r[3]))
    return rows


class TestEndToEnd:
    def test_single_buyer_no_leaks_yields_no_products(self, monkeypatch):
        # 1 visitor, single session, full funnel — < 3 views means
        # the product is filtered out as too-low-signal.
        rows = _make_event_rows({
            "v1": [
                ("product_view", "/p/x", 1000),
                ("add_to_cart", "/p/x", 2000),
                ("purchase", "/p/x", 3000),
            ],
        })
        _patch_io(monkeypatch, rows=rows)
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        # Only 1 view of /p/x → below the >=3 threshold → filtered
        assert out["products"] == []
        assert out["session_insights"]["total_buyer_sessions"] == 1
        assert out["session_insights"]["total_nonbuyer_sessions"] == 0

    def test_three_nonbuyers_surface_product_as_leak(self, monkeypatch):
        rows = _make_event_rows({
            "v1": [("product_view", "/p/wallet", 1000)],
            "v2": [("product_view", "/p/wallet", 2000)],
            "v3": [("product_view", "/p/wallet", 3000)],
        })
        _patch_io(monkeypatch, rows=rows)
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        assert len(out["products"]) == 1
        p = out["products"][0]
        assert p["product_url"] == "/p/wallet"
        assert p["views_7d"] == 3
        assert p["leak_point"] == "browse_to_cart"
        assert p["abandon_rate_pct"] == 100.0

    def test_exit_product_tracked(self, monkeypatch):
        # 3 visitors all exiting on /p/wallet
        rows = _make_event_rows({
            f"v{i}": [
                ("product_view", "/p/intro", 1000 + i * 100),
                ("product_view", "/p/wallet", 2000 + i * 100),
            ]
            for i in range(3)
        })
        _patch_io(monkeypatch, rows=rows)
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        top_exit = out["session_insights"]["top_exit_products"][0]
        assert top_exit["product_url"] == "/p/wallet"
        assert top_exit["exit_count"] == 3

    def test_buyer_session_stats(self, monkeypatch):
        rows = _make_event_rows({
            "buyer": [
                ("product_view", "/p/a", 1000),
                ("product_view", "/p/b", 1100),
                ("product_view", "/p/c", 1200),
                ("purchase", "/p/c", 1300),
            ],
            "nonbuyer": [
                ("product_view", "/p/a", 2000),
            ],
        })
        _patch_io(monkeypatch, rows=rows)
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        si = out["session_insights"]
        assert si["total_buyer_sessions"] == 1
        assert si["total_nonbuyer_sessions"] == 1
        assert si["buyer_avg_events"] == 4.0
        assert si["nonbuyer_avg_events"] == 1.0
        assert si["buyer_avg_products_viewed"] == 3.0


# ---------------------------------------------------------------------------
# Plan filter wired through composer
# ---------------------------------------------------------------------------


class TestPlanFilterPropagation:
    def test_lite_caps_products_at_3(self, monkeypatch):
        # 5 different products, each viewed 3 times by distinct visitors
        rows = _make_event_rows({
            f"v_{p}_{i}": [("product_view", f"/p/{p}", 1000 + i * 100)]
            for p in "abcde" for i in range(3)
        })
        _patch_io(monkeypatch, rows=rows)
        out_pro = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="pro")
        out_lite = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="lite")
        # Pro sees all 5; Lite sees only top-3
        assert len(out_pro["products"]) == 5
        assert len(out_lite["products"]) == 3
        # total_products_count stays honest across tiers (it's the
        # pre-cap number, used by the drawer "N products leaking" stat)
        assert out_pro["total_products_count"] == out_lite["total_products_count"]
        # session_insights redacted on Lite
        assert out_pro["session_insights"] != {}
        assert out_lite["session_insights"] == {}

    def test_lite_headline_stays_identical(self, monkeypatch):
        # Lite merchant should still see the scale of the leak even
        # with the product list truncated.
        rows = _make_event_rows({
            f"v{i}": [("product_view", "/p/leak", 1000 + i * 100)]
            for i in range(3)
        })
        _patch_io(monkeypatch, rows=rows)
        out_pro = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="pro")
        out_lite = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com", plan="lite")
        assert out_pro["headline"] == out_lite["headline"]


# ---------------------------------------------------------------------------
# Currency round-trip
# ---------------------------------------------------------------------------


class TestCurrencyRoundTrip:
    def test_currency_propagates_to_response(self, monkeypatch):
        _patch_io(monkeypatch, rows=[], currency="GBP")
        out = ai.compute_abandoned_intent(db=None, shop_domain="x.myshopify.com")
        assert out["currency"] == "GBP"
