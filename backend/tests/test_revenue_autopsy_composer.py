"""
Composer-level integration tests for `compute_product_autopsy`.

The 2026-05-12 A3 refactor decomposed the 265-LOC god function into a
composer + 7 pure helpers. The existing `test_revenue_autopsy_helpers.py`
locks every helper's contract in isolation. This file locks the
*composition* — how the composer wires those helpers together — so a
future refactor that re-shapes the orchestration can prove it preserves
the contract without booting Postgres.

Pattern: monkeypatch the 5 IO/cache/currency seams, drive the composer
with deterministic data, assert the response shape, cache behavior,
sort/cap, summary aggregation, and headline branching.

Born 2026-05-13 as a fix for the R-blocker:sprint>1d gap surfaced in
the 2026-05-12 god-function refactor sprint.
"""
from __future__ import annotations

from app.services import revenue_autopsy as ra


# ---------------------------------------------------------------------------
# helpers — build traffic + revenue maps the composer feeds to
# `_compute_one_autopsy`
# ---------------------------------------------------------------------------


def _t(vr, vp, ur=None, up=None):
    return {
        "views_recent": vr,
        "views_prior": vp,
        "uniques_recent": ur if ur is not None else vr,
        "uniques_prior": up if up is not None else vp,
    }


def _r(orec, opr, rrec, rpr):
    return {
        "orders_recent": orec,
        "orders_prior": opr,
        "revenue_recent": rrec,
        "revenue_prior": rpr,
    }


def _patch_io(
    monkeypatch,
    *,
    traffic=None,
    revenue=None,
    currency="EUR",
    cache_hit=None,
):
    """Wire the 5 IO seams so the composer is hermetic."""
    saved: dict = {"set": None}

    monkeypatch.setattr(ra, "_load_cached_autopsy", lambda shop: cache_hit)
    monkeypatch.setattr(
        ra, "_save_cached_autopsy",
        lambda shop, result: saved.update({"set": result}),
    )
    monkeypatch.setattr(
        ra, "_fetch_traffic_data",
        lambda db, shop, recent, prior: dict(traffic or {}),
    )
    monkeypatch.setattr(
        ra, "_fetch_revenue_data",
        lambda db, shop, recent, prior: dict(revenue or {}),
    )
    monkeypatch.setattr(
        ra, "_resolve_currency_formatter",
        lambda db, shop: (currency, lambda v, c: f"{v:.0f}"),
    )
    return saved


# ---------------------------------------------------------------------------
# Cache short-circuit
# ---------------------------------------------------------------------------


class TestCacheShortCircuit:
    def test_cache_hit_returns_immediately(self, monkeypatch):
        """When cache hits, composer returns cached payload without
        running fetch/compute/save."""
        cached_payload = {"shop_domain": "x.myshopify.com", "products": []}
        fetched = {"called": False}

        def _explode(*_a, **_kw):
            fetched["called"] = True
            raise AssertionError("fetch_traffic_data must NOT run on cache hit")

        monkeypatch.setattr(ra, "_load_cached_autopsy", lambda s: cached_payload)
        monkeypatch.setattr(ra, "_fetch_traffic_data", _explode)
        monkeypatch.setattr(ra, "_fetch_revenue_data", _explode)
        monkeypatch.setattr(
            ra, "_save_cached_autopsy",
            lambda *_a, **_kw: pytest_fail_on_call(),
        )

        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert out is cached_payload
        assert fetched["called"] is False

    def test_cache_miss_runs_full_pipeline_and_writes_cache(self, monkeypatch):
        saved = _patch_io(monkeypatch, traffic={}, revenue={}, cache_hit=None)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert saved["set"] is not None, "cache MUST be written on miss"
        assert saved["set"] == out


def pytest_fail_on_call():  # helper used by the explode-monkeypatch above
    raise AssertionError("_save_cached_autopsy must NOT run on cache hit")


# ---------------------------------------------------------------------------
# Response shape — top-level + nested keys
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_top_level_keys_present(self, monkeypatch):
        _patch_io(monkeypatch)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert set(out.keys()) == {
            "shop_domain", "products", "summary", "headline",
            "currency", "generated_at",
        }

    def test_shop_domain_round_tripped(self, monkeypatch):
        _patch_io(monkeypatch)
        out = ra.compute_product_autopsy(None, "round-trip.myshopify.com")
        assert out["shop_domain"] == "round-trip.myshopify.com"

    def test_currency_round_tripped(self, monkeypatch):
        _patch_io(monkeypatch, currency="GBP")
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert out["currency"] == "GBP"

    def test_generated_at_is_iso_string(self, monkeypatch):
        _patch_io(monkeypatch)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        # ISO 8601 minimal sanity: contains "T", parseable as date prefix
        assert "T" in out["generated_at"]
        from datetime import datetime
        datetime.fromisoformat(out["generated_at"])

    def test_summary_keys_present(self, monkeypatch):
        _patch_io(monkeypatch)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert set(out["summary"].keys()) == {
            "declining_count", "growing_count",
            "total_loss_per_week", "total_gain_per_week",
            "top_decline_cause",
        }


# ---------------------------------------------------------------------------
# Empty-state — no traffic, no revenue
# ---------------------------------------------------------------------------


class TestEmptyState:
    def test_empty_inputs_yield_empty_products_and_fallback_headline(self, monkeypatch):
        _patch_io(monkeypatch, traffic={}, revenue={})
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert out["products"] == []
        assert out["summary"]["declining_count"] == 0
        assert out["summary"]["growing_count"] == 0
        assert out["summary"]["total_loss_per_week"] == 0
        assert out["summary"]["top_decline_cause"] == "none"
        assert "Insufficient" in out["headline"]

    def test_below_threshold_products_excluded(self, monkeypatch):
        # views=2 + orders=1 → below the (5 views OR 2 orders) gate
        traffic = {"/products/tiny": _t(vr=1, vp=1)}
        revenue = {"/products/tiny": _r(1, 0, 5.0, 0.0)}
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert out["products"] == []


# ---------------------------------------------------------------------------
# Sort + cap to _MAX_PRODUCTS
# ---------------------------------------------------------------------------


class TestSortAndCap:
    def test_products_sorted_by_abs_delta_descending(self, monkeypatch):
        """3 products: one big decline, one small growth, one big growth.
        Composer MUST sort by |delta| descending."""
        traffic = {
            "/products/a": _t(vr=100, vp=100),
            "/products/b": _t(vr=100, vp=100),
            "/products/c": _t(vr=100, vp=100),
        }
        revenue = {
            "/products/a": _r(10, 10, 1000.0, 100.0),   # +900 (biggest growth)
            "/products/b": _r(10, 10, 200.0, 100.0),    # +100 (small growth)
            "/products/c": _r(10, 10, 50.0, 800.0),     # -750 (decline)
        }
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        deltas = [abs(p["revenue_delta_eur"]) for p in out["products"]]
        assert deltas == sorted(deltas, reverse=True), (
            f"products MUST be sorted by |delta| descending: {deltas}"
        )

    def test_cap_to_max_products(self, monkeypatch):
        """Build 20 valid products; composer MUST cap at _MAX_PRODUCTS=15."""
        traffic = {}
        revenue = {}
        for i in range(20):
            url = f"/products/p{i:02d}"
            traffic[url] = _t(vr=50, vp=50)
            revenue[url] = _r(5, 5, 500.0 + i * 100, 100.0)
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert len(out["products"]) == ra._MAX_PRODUCTS == 15

    def test_cap_preserves_largest_deltas(self, monkeypatch):
        """After cap, the kept products MUST be those with largest |delta|."""
        traffic = {}
        revenue = {}
        for i in range(20):
            url = f"/products/p{i:02d}"
            traffic[url] = _t(vr=50, vp=50)
            # delta grows linearly with i — biggest indices have biggest delta
            revenue[url] = _r(5, 5, 100.0 + i * 200, 100.0)
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        kept_urls = {p["product_url"] for p in out["products"]}
        # Indices 5..19 have the largest |delta|; the smallest 5 must be excluded.
        for i in range(5):
            assert f"/products/p{i:02d}" not in kept_urls


# ---------------------------------------------------------------------------
# URL-set union (a URL with revenue but no traffic, vice versa)
# ---------------------------------------------------------------------------


class TestUrlUnion:
    def test_revenue_only_url_included(self, monkeypatch):
        """A product with orders but no tracked views still enters the
        compute pass via _ZERO_TRAFFIC fallback."""
        traffic: dict = {}
        revenue = {"/products/revenue-only": _r(5, 5, 1000.0, 100.0)}
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        urls = {p["product_url"] for p in out["products"]}
        assert "/products/revenue-only" in urls

    def test_traffic_only_url_excluded_by_threshold(self, monkeypatch):
        """A product with views but zero orders → traffic_change_pct gate
        keeps it if |change| >= 10%; rev_delta=0 < 1 so still excluded
        when traffic change is small."""
        traffic = {"/products/lookers": _t(vr=100, vp=100)}
        revenue: dict = {}
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        # |rev_delta|=0 < 1 AND traffic_change_pct=0 < 10 → excluded
        assert {p["product_url"] for p in out["products"]} == set()


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


class TestSummaryAggregation:
    def test_declining_and_growing_partition(self, monkeypatch):
        traffic = {
            "/products/a": _t(vr=100, vp=100),
            "/products/b": _t(vr=100, vp=100),
        }
        revenue = {
            "/products/a": _r(5, 10, 50.0, 200.0),     # -150 declining
            "/products/b": _r(10, 5, 300.0, 50.0),     # +250 growing
        }
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        s = out["summary"]
        assert s["declining_count"] == 1
        assert s["growing_count"] == 1
        assert s["total_loss_per_week"] == 150.0
        assert s["total_gain_per_week"] == 250.0

    def test_top_decline_cause_is_most_common_among_declining(self, monkeypatch):
        """3 declining products with different primary causes → top_decline_cause
        is whichever appears most often. Build 2 traffic-driven declines + 1
        conversion-driven decline → top must be 'traffic'."""
        traffic = {
            # 2 products with huge view drop → traffic-driven decline
            "/products/a": _t(vr=20, vp=200),
            "/products/b": _t(vr=20, vp=200),
            # 1 product with stable views but conversion drop
            "/products/c": _t(vr=200, vp=200),
        }
        revenue = {
            "/products/a": _r(2, 20, 100.0, 1000.0),
            "/products/b": _r(2, 20, 100.0, 1000.0),
            "/products/c": _r(2, 20, 100.0, 1000.0),
        }
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert out["summary"]["declining_count"] == 3
        assert out["summary"]["top_decline_cause"] == "traffic"


# ---------------------------------------------------------------------------
# Headline branching
# ---------------------------------------------------------------------------


class TestHeadlineBranching:
    def test_headline_declining_branch(self, monkeypatch):
        traffic = {"/products/dec": _t(vr=20, vp=200)}
        revenue = {"/products/dec": _r(2, 20, 100.0, 1000.0)}
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue, currency="EUR")
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert "declining" in out["headline"].lower()
        assert "main cause" in out["headline"].lower()

    def test_headline_growing_branch(self, monkeypatch):
        traffic = {"/products/grow": _t(vr=200, vp=100)}
        revenue = {"/products/grow": _r(20, 5, 1500.0, 100.0)}
        _patch_io(monkeypatch, traffic=traffic, revenue=revenue, currency="EUR")
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert "growing" in out["headline"].lower()

    def test_headline_empty_branch(self, monkeypatch):
        _patch_io(monkeypatch, traffic={}, revenue={})
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert "insufficient" in out["headline"].lower()


# ---------------------------------------------------------------------------
# Cache invariant — written payload === returned payload
# ---------------------------------------------------------------------------


class TestCacheWriteContract:
    def test_cached_payload_is_returned_payload(self, monkeypatch):
        """The cache MUST receive byte-identical content with what the
        caller receives — a future cache hit should produce the same shape."""
        traffic = {"/products/a": _t(vr=100, vp=100)}
        revenue = {"/products/a": _r(10, 5, 1000.0, 200.0)}
        saved = _patch_io(monkeypatch, traffic=traffic, revenue=revenue)
        out = ra.compute_product_autopsy(None, "x.myshopify.com")
        assert saved["set"] == out
