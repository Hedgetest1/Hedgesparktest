"""
Unit tests for the pure helpers extracted from `compute_abandoned_intent`
in the 2026-05-13 A3 refactor.

The composer is locked by test_abandoned_intent_composer.py + the
10 prior e2e tests in test_abandoned_intent.py. This file is the
structural-unit gate.
"""
from __future__ import annotations

from app.services.abandoned_intent import (
    _accumulate_session_stats,
    _build_intent_headline,
    _build_product_record,
    _build_products_list,
    _build_session_insights,
    _cache_key_for,
    _classify_leak,
    _empty_intent_response,
    _group_events_by_visitor,
    _humanize_url,
    _SessionAccumulator,
    _split_into_sessions,
    _SESSION_GAP_MS,
)


# ---------------------------------------------------------------------------
# _humanize_url
# ---------------------------------------------------------------------------


class TestHumanizeUrl:
    def test_canonical_slug(self):
        assert _humanize_url("/products/premium-leather-wallet") == "Premium Leather Wallet"

    def test_underscore_separator(self):
        assert _humanize_url("/products/cool_thing") == "Cool Thing"

    def test_empty_returns_input(self):
        assert _humanize_url("") == ""


# ---------------------------------------------------------------------------
# _cache_key_for
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_key_includes_prefix(self):
        assert _cache_key_for("x.myshopify.com").startswith("hs:intent:v1:")

    def test_same_shop_yields_same_key(self):
        assert _cache_key_for("x.myshopify.com") == _cache_key_for("x.myshopify.com")

    def test_different_shop_yields_different_key(self):
        assert _cache_key_for("a.myshopify.com") != _cache_key_for("b.myshopify.com")


# ---------------------------------------------------------------------------
# _empty_intent_response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    def test_empty_shape(self):
        from datetime import datetime
        out = _empty_intent_response("x.myshopify.com", "EUR", datetime(2025, 1, 1))
        assert out["products"] == []
        assert out["total_products_count"] == 0
        assert out["session_insights"] == {}
        assert "Insufficient data" in out["headline"]
        assert out["currency"] == "EUR"
        assert out["shop_domain"] == "x.myshopify.com"


# ---------------------------------------------------------------------------
# _group_events_by_visitor
# ---------------------------------------------------------------------------


class TestGroupByVisitor:
    def test_single_visitor_collects_events(self):
        rows = [
            ("v1", "product_view", "/p/a", 1000),
            ("v1", "add_to_cart", "/p/a", 2000),
        ]
        out = _group_events_by_visitor(rows)
        assert list(out.keys()) == ["v1"]
        assert len(out["v1"]) == 2
        assert out["v1"][0]["event_type"] == "product_view"

    def test_two_visitors_separated(self):
        rows = [
            ("v1", "product_view", "/p/a", 1000),
            ("v2", "product_view", "/p/b", 1100),
        ]
        out = _group_events_by_visitor(rows)
        assert set(out.keys()) == {"v1", "v2"}

    def test_null_product_url_becomes_empty_string(self):
        rows = [("v1", "checkout", None, 1000)]
        out = _group_events_by_visitor(rows)
        assert out["v1"][0]["product_url"] == ""


# ---------------------------------------------------------------------------
# _split_into_sessions — 30-min gap boundary
# ---------------------------------------------------------------------------


class TestSplitSessions:
    def test_single_event_one_session(self):
        events = [{"event_type": "product_view", "product_url": "/x", "timestamp": 1000}]
        sessions = _split_into_sessions(events)
        assert len(sessions) == 1
        assert sessions[0] == events

    def test_close_events_one_session(self):
        # 5 minutes apart < 30 min
        events = [
            {"event_type": "product_view", "product_url": "/x", "timestamp": 1000},
            {"event_type": "product_view", "product_url": "/y", "timestamp": 1000 + 5 * 60 * 1000},
        ]
        sessions = _split_into_sessions(events)
        assert len(sessions) == 1
        assert len(sessions[0]) == 2

    def test_gap_over_threshold_splits(self):
        # 31 minutes apart > 30 min
        events = [
            {"event_type": "product_view", "product_url": "/x", "timestamp": 1000},
            {"event_type": "product_view", "product_url": "/y",
             "timestamp": 1000 + 31 * 60 * 1000},
        ]
        sessions = _split_into_sessions(events)
        assert len(sessions) == 2

    def test_exactly_at_threshold_one_session(self):
        # Exactly 30 min → not > _SESSION_GAP_MS → same session
        events = [
            {"event_type": "product_view", "product_url": "/x", "timestamp": 1000},
            {"event_type": "product_view", "product_url": "/y",
             "timestamp": 1000 + _SESSION_GAP_MS},
        ]
        sessions = _split_into_sessions(events)
        assert len(sessions) == 1


# ---------------------------------------------------------------------------
# _classify_leak — 3-branch decision
# ---------------------------------------------------------------------------


class TestClassifyLeak:
    def test_browse_to_cart_when_view_to_cart_low(self):
        leak, label = _classify_leak(view_to_cart=2.0, cart_to_purchase=80.0)
        assert leak == "browse_to_cart"
        assert "don't add to cart" in label

    def test_cart_to_purchase_when_cart_drop(self):
        leak, label = _classify_leak(view_to_cart=10.0, cart_to_purchase=20.0)
        assert leak == "cart_to_purchase"
        assert "not purchased" in label

    def test_healthy_when_both_above_thresholds(self):
        leak, label = _classify_leak(view_to_cart=10.0, cart_to_purchase=80.0)
        assert leak == "none"
        assert "healthy" in label.lower()

    def test_browse_to_cart_threshold_boundary(self):
        # view_to_cart < 5 → browse_to_cart
        leak, _ = _classify_leak(view_to_cart=4.9, cart_to_purchase=80.0)
        assert leak == "browse_to_cart"
        # view_to_cart == 5 → falls through (cart_to_purchase decides)
        leak, _ = _classify_leak(view_to_cart=5.0, cart_to_purchase=80.0)
        assert leak == "none"

    def test_cart_to_purchase_threshold_boundary(self):
        leak, _ = _classify_leak(view_to_cart=10.0, cart_to_purchase=29.9)
        assert leak == "cart_to_purchase"
        leak, _ = _classify_leak(view_to_cart=10.0, cart_to_purchase=30.0)
        assert leak == "none"


# ---------------------------------------------------------------------------
# _build_product_record — shape + math
# ---------------------------------------------------------------------------


class TestBuildProductRecord:
    def test_record_contains_all_keys(self):
        ps = {
            "views": 10, "carts": 4, "purchases": 1,
            "view_only_visitors": {"a", "b"},
            "cart_abandon_visitors": {"c"},
            "buyer_visitors": {"d"},
            "last_viewed_before_exit": 0,
        }
        out = _build_product_record("/p/x", ps, exit_count=2)
        assert set(out.keys()) == {
            "product_url", "product_name", "views_7d", "carts_7d",
            "purchases_7d", "view_to_cart_pct", "cart_to_purchase_pct",
            "abandon_rate_pct", "exit_sessions", "leak_point",
            "leak_label", "unique_viewers", "cart_abandoners",
        }

    def test_unique_viewers_sums_three_sets(self):
        ps = {
            "views": 5, "carts": 0, "purchases": 0,
            "view_only_visitors": {"a", "b"},
            "cart_abandon_visitors": {"c"},
            "buyer_visitors": set(),
            "last_viewed_before_exit": 0,
        }
        out = _build_product_record("/p/x", ps, exit_count=0)
        assert out["unique_viewers"] == 3

    def test_abandon_rate_when_no_purchases(self):
        ps = {
            "views": 10, "carts": 5, "purchases": 0,
            "view_only_visitors": set(), "cart_abandon_visitors": set(),
            "buyer_visitors": set(), "last_viewed_before_exit": 0,
        }
        out = _build_product_record("/p/x", ps, 0)
        assert out["abandon_rate_pct"] == 100.0

    def test_humanize_product_name(self):
        ps = {
            "views": 5, "carts": 0, "purchases": 0,
            "view_only_visitors": set(), "cart_abandon_visitors": set(),
            "buyer_visitors": set(), "last_viewed_before_exit": 0,
        }
        out = _build_product_record("/products/cool-thing", ps, 0)
        assert out["product_name"] == "Cool Thing"


# ---------------------------------------------------------------------------
# _build_products_list — filter + sort + cap
# ---------------------------------------------------------------------------


def _make_stat(views, carts, purchases, vid_count=1):
    return {
        "views": views, "carts": carts, "purchases": purchases,
        "view_only_visitors": {f"v{i}" for i in range(vid_count)},
        "cart_abandon_visitors": set(),
        "buyer_visitors": set(),
        "last_viewed_before_exit": 0,
    }


class TestBuildProductsList:
    def test_filters_below_min_views(self):
        ps = {
            "/p/a": _make_stat(views=2, carts=0, purchases=0),  # < 3
            "/p/b": _make_stat(views=5, carts=1, purchases=0),
        }
        products, true_count = _build_products_list(ps, exit_products={})
        urls = {p["product_url"] for p in products}
        assert "/p/a" not in urls
        assert "/p/b" in urls
        assert true_count == 1

    def test_sorts_by_opportunity_descending(self):
        ps = {
            "/p/big_leak": _make_stat(views=100, carts=0, purchases=0),  # 100*1=100
            "/p/small_leak": _make_stat(views=10, carts=0, purchases=0),  # 10*1=10
        }
        products, _ = _build_products_list(ps, {})
        assert products[0]["product_url"] == "/p/big_leak"

    def test_true_count_preserved_before_cap(self):
        # 20 products, all leaking → true_count=20, capped at 15
        ps = {f"/p/{i}": _make_stat(views=10, carts=0, purchases=0) for i in range(20)}
        products, true_count = _build_products_list(ps, {})
        assert true_count == 20
        assert len(products) == 15

    def test_exit_count_propagated(self):
        ps = {"/p/x": _make_stat(views=10, carts=0, purchases=0)}
        out, _ = _build_products_list(ps, {"/p/x": 7})
        assert out[0]["exit_sessions"] == 7


# ---------------------------------------------------------------------------
# _build_session_insights — avg + top-5 exits
# ---------------------------------------------------------------------------


class TestSessionInsights:
    def test_empty_lists_yield_zeros(self):
        out = _build_session_insights(
            exit_products={},
            buyer_session_lengths=[], nonbuyer_session_lengths=[],
            buyer_products_viewed=[], nonbuyer_products_viewed=[],
        )
        assert out["buyer_avg_events"] == 0.0
        assert out["nonbuyer_avg_events"] == 0.0
        assert out["total_buyer_sessions"] == 0
        assert out["top_exit_products"] == []

    def test_averages_computed(self):
        out = _build_session_insights(
            exit_products={},
            buyer_session_lengths=[2, 4, 6],
            nonbuyer_session_lengths=[1, 1],
            buyer_products_viewed=[1, 2, 3],
            nonbuyer_products_viewed=[1, 1],
        )
        assert out["buyer_avg_events"] == 4.0
        assert out["nonbuyer_avg_events"] == 1.0
        assert out["buyer_avg_products_viewed"] == 2.0

    def test_top_exits_capped_at_5(self):
        exits = {f"/p/{i}": 10 - i for i in range(10)}
        out = _build_session_insights(
            exit_products=exits,
            buyer_session_lengths=[], nonbuyer_session_lengths=[],
            buyer_products_viewed=[], nonbuyer_products_viewed=[],
        )
        assert len(out["top_exit_products"]) == 5
        # Sorted descending — first has highest count
        assert out["top_exit_products"][0]["exit_count"] == 10


# ---------------------------------------------------------------------------
# _build_intent_headline
# ---------------------------------------------------------------------------


class TestIntentHeadline:
    def test_empty_products_fallback(self):
        out = _build_intent_headline([])
        assert "Not enough data" in out

    def test_worst_product_in_headline(self):
        products = [{
            "product_name": "Wallet", "views_7d": 50, "abandon_rate_pct": 95.0,
            "leak_label": "Visitors view but don't add to cart",
        }]
        out = _build_intent_headline(products)
        assert "Wallet" in out
        assert "50 views" in out
        assert "95%" in out
        assert "don't add to cart" in out.lower()


# ---------------------------------------------------------------------------
# _accumulate_session_stats — NamedTuple contract
# ---------------------------------------------------------------------------


class TestSessionAccumulator:
    def test_empty_input_returns_empty_named_tuple(self):
        out = _accumulate_session_stats({})
        # Field-name access works
        assert out.product_stats == {}
        assert out.exit_products == {}
        assert out.buyer_session_lengths == []
        assert out.nonbuyer_session_lengths == []
        assert out.buyer_products_viewed == []
        assert out.nonbuyer_products_viewed == []
        # Type is the NamedTuple class — not a plain tuple
        assert isinstance(out, _SessionAccumulator)

    def test_named_tuple_field_order_locked(self):
        """If a future refactor re-orders fields, this test fails.
        Positional destructuring is preserved (tuple compatibility)
        but downstream code uses named access (composer + tests)."""
        # Field order MUST match the dataclass declaration so that
        # any caller still doing positional destructuring works.
        assert _SessionAccumulator._fields == (
            "product_stats", "exit_products",
            "buyer_session_lengths", "nonbuyer_session_lengths",
            "buyer_products_viewed", "nonbuyer_products_viewed",
        )

    def test_single_buyer_session_classified(self):
        events = {"v1": [
            {"event_type": "product_view", "product_url": "/p/a", "timestamp": 1000},
            {"event_type": "purchase", "product_url": "/p/a", "timestamp": 2000},
        ]}
        acc = _accumulate_session_stats(events)
        # 1 buyer session, 0 nonbuyer
        assert len(acc.buyer_session_lengths) == 1
        assert acc.buyer_session_lengths[0] == 2  # 2 events
        assert acc.nonbuyer_session_lengths == []
        # buyer_visitors set contains v1
        assert "v1" in acc.product_stats["/p/a"]["buyer_visitors"]

    def test_single_nonbuyer_exit_product_tracked(self):
        events = {"v1": [
            {"event_type": "product_view", "product_url": "/p/x", "timestamp": 1000},
            {"event_type": "product_view", "product_url": "/p/exit", "timestamp": 2000},
        ]}
        acc = _accumulate_session_stats(events)
        # Non-buying session → exit_products counts the last product
        assert acc.exit_products["/p/exit"] == 1
        # Not /p/x — only the last is "exit"
        assert "/p/x" not in acc.exit_products

    def test_cart_abandoner_classification(self):
        events = {"v1": [
            {"event_type": "product_view", "product_url": "/p/a", "timestamp": 1000},
            {"event_type": "add_to_cart", "product_url": "/p/a", "timestamp": 2000},
        ]}
        acc = _accumulate_session_stats(events)
        # No purchase + ATC → cart_abandon_visitors set
        assert "v1" in acc.product_stats["/p/a"]["cart_abandon_visitors"]
        assert acc.product_stats["/p/a"]["carts"] == 1
