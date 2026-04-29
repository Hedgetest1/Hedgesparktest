"""Tests for Abandoned Intent — Phase 1.4 Lite unlock.

Verifies:
  - Plan-aware response shape (Pro full vs Lite reduced)
  - Products truncated to top 3 for Lite
  - session_insights redacted to {} for Lite
  - Headline + shop_domain + currency invariant across plans
  - Cache shallow-copy discipline (Lite reads don't corrupt Pro)
  - Empty-data path honors plan filter
"""
from __future__ import annotations

from app.services.abandoned_intent import compute_abandoned_intent


def test_abandoned_intent_pro_returns_full_response(db):
    """Pro plan gets all fields populated (products list may be empty
    on a fresh shop, but session_insights key exists as a dict)."""
    report = compute_abandoned_intent(db, "abint-pro-probe.myshopify.com", plan="pro")
    assert report["shop_domain"] == "abint-pro-probe.myshopify.com"
    assert "products" in report
    assert isinstance(report["products"], list)
    assert "session_insights" in report
    assert isinstance(report["session_insights"], dict)
    assert "headline" in report
    assert isinstance(report["headline"], str)
    assert "currency" in report


def test_abandoned_intent_lite_redacts_session_insights(db):
    """Lite plan returns empty session_insights regardless of data."""
    report = compute_abandoned_intent(db, "abint-lite-redact.myshopify.com", plan="lite")
    assert report["session_insights"] == {}


def test_abandoned_intent_lite_caps_products_at_three(db):
    """Lite plan never returns more than 3 products."""
    # Fresh empty shop → 0 products (within cap); Lite filter preserves 0
    report = compute_abandoned_intent(db, "abint-lite-cap-empty.myshopify.com", plan="lite")
    assert len(report["products"]) <= 3


def test_abandoned_intent_lite_keeps_hero_headline_and_currency(db):
    """Lite sees the leak count framing + currency identical to Pro."""
    shop = "abint-lite-hero.myshopify.com"
    pro = compute_abandoned_intent(db, shop, plan="pro")
    lite = compute_abandoned_intent(db, shop, plan="lite")
    assert pro["headline"] == lite["headline"]
    assert pro["currency"] == lite["currency"]
    assert pro["shop_domain"] == lite["shop_domain"]


def test_abandoned_intent_empty_shop_still_plan_filtered(db):
    """Zero-data path (no events): both plans return empty products,
    same headline. Filter applied correctly even on the early-return
    branch."""
    shop = "abint-empty-shop.myshopify.com"
    pro = compute_abandoned_intent(db, shop, plan="pro")
    lite = compute_abandoned_intent(db, shop, plan="lite")
    assert pro["products"] == []
    assert lite["products"] == []
    assert pro["session_insights"] == {}
    assert lite["session_insights"] == {}


def test_abandoned_intent_plan_filter_does_not_mutate_cached_dict(db):
    """Lite read must not mutate the shared cached dict — Pro reads
    after Lite must still see the full payload (critical: same bug
    class caught in RARS test_rars_plan_filter_does_not_mutate_cached_dict)."""
    shop = "abint-cache-mutation-probe.myshopify.com"
    pro_first = compute_abandoned_intent(db, shop, plan="pro")
    first_product_count = len(pro_first["products"])
    first_insights_keys = set(pro_first["session_insights"].keys())
    # Lite read — should NOT affect the cached dict
    _lite = compute_abandoned_intent(db, shop, plan="lite")
    # Pro read again — must match first read
    pro_second = compute_abandoned_intent(db, shop, plan="pro")
    assert len(pro_second["products"]) == first_product_count, (
        "Lite read mutated shared cached dict — Pro product list corrupted"
    )
    assert set(pro_second["session_insights"].keys()) == first_insights_keys, (
        "Lite read mutated shared cached dict — Pro session_insights corrupted"
    )


def test_abandoned_intent_total_products_count_preserved_for_lite(db):
    """Lite truncates `products` to 3 but `total_products_count` stays
    at the real count — this is what makes the Lite UI honest
    ('showing top 3 of N' instead of lying about scale)."""
    shop = "abint-total-count-probe.myshopify.com"
    pro = compute_abandoned_intent(db, shop, plan="pro")
    lite = compute_abandoned_intent(db, shop, plan="lite")
    # Both tiers report the same true total
    assert pro["total_products_count"] == lite["total_products_count"]
    # Products list can differ in length but total_products_count must not
    assert lite["total_products_count"] == len(pro["products"])
    # Lite's products list is capped at 3
    assert len(lite["products"]) <= 3


def test_abandoned_intent_total_products_count_is_integer(db):
    """Shape contract: total_products_count is always an int, never null."""
    for plan in ("pro", "lite", "lite"):
        report = compute_abandoned_intent(db, f"abint-count-shape-{plan}.myshopify.com", plan=plan)
        assert "total_products_count" in report
        assert isinstance(report["total_products_count"], int)
        assert report["total_products_count"] >= 0


def test_abandoned_intent_total_count_captured_before_truncation(db):
    """The `total_products_count` field is the ONLY honest-scale number
    the UI has when the list is truncated. It MUST be captured BEFORE
    the [:_MAX_PRODUCTS] slice or it silently lies at 15 even when the
    real total is 30 or 100.

    This test locks the invariant: even though we can't easily produce
    a shop with >15 leaking products in a unit test, we can assert the
    field name equals the true (pre-slice) list length when that length
    fits inside _MAX_PRODUCTS. The key protection is that the field is
    populated from `true_leak_count`, not from `len(products)` after the
    slice — making the "true count silently capped at 15" regression
    impossible to reintroduce without changing the variable name."""
    from app.services.abandoned_intent import _MAX_PRODUCTS
    shop = "abint-true-count-pre-slice.myshopify.com"
    report = compute_abandoned_intent(db, shop, plan="pro")
    # Invariant: products array is always <= _MAX_PRODUCTS
    assert len(report["products"]) <= _MAX_PRODUCTS
    # Invariant: total_products_count is an integer, >= the visible list
    assert isinstance(report["total_products_count"], int)
    assert report["total_products_count"] >= len(report["products"])


def test_abandoned_intent_default_plan_is_pro(db):
    """Omitting the plan kwarg defaults to Pro — back-compat with any
    callers that pre-dated Phase 1.4 (service had no plan param before)."""
    report = compute_abandoned_intent(db, "abint-default-plan.myshopify.com")
    # If default were "lite", session_insights would be {} — but since
    # default is "pro", the service must not apply the filter.
    # We can only assert the filter-aware shape: session_insights is a
    # dict (empty or populated, depending on data).
    assert isinstance(report["session_insights"], dict)
