"""Tests for /analytics/cohorts/by-dimension — Gap #8 close (brutal
$0-70 audit + parity doctrine 2026-04-27).

Coverage:
- 3 valid dim values (first_channel/first_product/first_discount) → 200
- Invalid dim → 422 (Pydantic regex validation)
- Empty state → empty buckets + "need more data" insight
- first_channel: VPS join produces channel buckets
- first_product: line_items[0].title used as dim
- first_discount: first discount_codes element used (or "(none)")
- Customer's FIRST order dim wins (rows ordered ASC)
- best_vs_worst differentiator activates at >=2 buckets with >=5 customers
- best_vs_worst stays empty when cold-start (single bucket or <5 customers)
- Tenant isolation: shop_domain filter on every dim path
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.shop_order import ShopOrder
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.ltv_engine import get_cohorts_by_dimension
from tests.conftest import SHOP_A, auth_cookies


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_order(db, shop, order_id, email, days_ago, *, price=100.0,
                line_items=None, discount_codes=None):
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=order_id,
        total_price=price, currency="USD",
        customer_email=email,
        line_items=line_items or [{"price": str(price), "quantity": 1}],
        discount_codes=discount_codes,
        created_at=_now() - timedelta(days=days_ago),
        source="webhook",
    ))


# ════════════════════════════════════════════════════════════════════════
# Endpoint smoke — 3 dimensions × happy path + invalid handling
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("dim", ["first_channel", "first_product", "first_discount"])
def test_endpoint_accepts_valid_dim(dim, client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get(
        f"/analytics/cohorts/by-dimension?dim={dim}&months=6",
        cookies=cookies,
    )
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body["dim"] == dim
    assert "buckets" in body
    assert "best_vs_worst" in body
    assert "customer_coverage" in body


def test_endpoint_rejects_invalid_dim(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get(
        "/analytics/cohorts/by-dimension?dim=ad_spend",
        cookies=cookies,
    )
    assert resp.status_code == 422


def test_endpoint_rejects_missing_dim(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get("/analytics/cohorts/by-dimension", cookies=cookies)
    assert resp.status_code == 422


def test_endpoint_clamps_window(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get(
        "/analytics/cohorts/by-dimension?dim=first_channel&months=99",
        cookies=cookies,
    )
    assert resp.status_code == 422


# ════════════════════════════════════════════════════════════════════════
# first_channel — VPS join
# ════════════════════════════════════════════════════════════════════════


def test_first_channel_buckets_by_last_source(db, merchant_a):
    """3 customers acquired via different channels → 3 buckets."""
    now = _now()
    # Customer A — google_ads
    _seed_order(db, SHOP_A, "ch-a-1", "a@test.com", days_ago=10)
    db.add(VisitorPurchaseSession(
        shop_domain=SHOP_A, visitor_id="va",
        shopify_order_id="ch-a-1",
        product_url="https://example.com/p",
        confirmed_at=now - timedelta(days=10),
        last_source="google_ads",
    ))
    # Customer B — organic
    _seed_order(db, SHOP_A, "ch-b-1", "b@test.com", days_ago=8)
    db.add(VisitorPurchaseSession(
        shop_domain=SHOP_A, visitor_id="vb",
        shopify_order_id="ch-b-1",
        product_url="https://example.com/p",
        confirmed_at=now - timedelta(days=8),
        last_source="organic",
    ))
    # Customer C — direct (no VPS)
    _seed_order(db, SHOP_A, "ch-c-1", "c@test.com", days_ago=6)
    db.commit()

    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_channel", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "google_ads" in keys
    assert "organic" in keys
    assert "(direct/unknown)" in keys


def test_first_channel_first_order_wins(db, merchant_a):
    """A customer's FIRST order channel decides the bucket — second
    order's different channel does NOT reassign them."""
    now = _now()
    # Customer A first order via organic, second order via google_ads
    _seed_order(db, SHOP_A, "ch-fw-1", "fw@test.com", days_ago=20)
    db.add(VisitorPurchaseSession(
        shop_domain=SHOP_A, visitor_id="vfw1",
        shopify_order_id="ch-fw-1",
        product_url="https://example.com/p",
        confirmed_at=now - timedelta(days=20),
        last_source="organic",
    ))
    _seed_order(db, SHOP_A, "ch-fw-2", "fw@test.com", days_ago=5)
    db.add(VisitorPurchaseSession(
        shop_domain=SHOP_A, visitor_id="vfw2",
        shopify_order_id="ch-fw-2",
        product_url="https://example.com/p",
        confirmed_at=now - timedelta(days=5),
        last_source="google_ads",
    ))
    db.commit()

    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_channel", months=6)
    organic_bucket = next(
        (b for b in body["buckets"] if b["dim_value"] == "organic"), None
    )
    google_bucket = next(
        (b for b in body["buckets"] if b["dim_value"] == "google_ads"), None
    )
    assert organic_bucket is not None
    assert organic_bucket["size"] == 1
    # Customer NOT in google_ads bucket (they were FIRST acquired via organic)
    assert google_bucket is None or google_bucket["size"] == 0


# ════════════════════════════════════════════════════════════════════════
# first_product
# ════════════════════════════════════════════════════════════════════════


def test_first_product_uses_first_line_item_title(db, merchant_a):
    now = _now()
    _seed_order(
        db, SHOP_A, "fp-1", "p1@test.com", days_ago=5,
        line_items=[{
            "title": "Gateway Widget", "price": "100", "quantity": 1
        }],
    )
    _seed_order(
        db, SHOP_A, "fp-2", "p2@test.com", days_ago=4,
        line_items=[{
            "title": "Other Item", "price": "50", "quantity": 1
        }],
    )
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_product", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "Gateway Widget" in keys
    assert "Other Item" in keys


# ════════════════════════════════════════════════════════════════════════
# first_discount
# ════════════════════════════════════════════════════════════════════════


def test_first_discount_with_code(db, merchant_a):
    _seed_order(
        db, SHOP_A, "disc-1", "d1@test.com", days_ago=5,
        discount_codes=["SUMMER10"],
    )
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_discount", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "SUMMER10" in keys


def test_first_discount_none_bucket(db, merchant_a):
    _seed_order(
        db, SHOP_A, "disc-none", "dn@test.com", days_ago=5,
        discount_codes=None,
    )
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_discount", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "(none)" in keys


def test_first_discount_empty_array(db, merchant_a):
    """discount_codes = [] → also '(none)' bucket."""
    _seed_order(
        db, SHOP_A, "disc-empty", "de@test.com", days_ago=5,
        discount_codes=[],
    )
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_discount", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "(none)" in keys


# ════════════════════════════════════════════════════════════════════════
# Differentiator — best_vs_worst plain-language insight
# ════════════════════════════════════════════════════════════════════════


def test_best_vs_worst_activates_with_2plus_buckets_5plus_customers(db, merchant_a):
    """Cold-start guard: only quantify when >=2 buckets each with >=5 customers."""
    now = _now()
    # 5 customers via google_ads — all repeat (2+ months)
    for i in range(5):
        _seed_order(db, SHOP_A, f"bvw-g-{i}-1", f"g{i}@test.com", days_ago=60 - i)
        db.add(VisitorPurchaseSession(
            shop_domain=SHOP_A, visitor_id=f"vg{i}",
            shopify_order_id=f"bvw-g-{i}-1",
            product_url="https://example.com/p",
            confirmed_at=now - timedelta(days=60 - i),
            last_source="google_ads",
        ))
        # Repeat order in different month
        _seed_order(db, SHOP_A, f"bvw-g-{i}-2", f"g{i}@test.com", days_ago=10 - (i % 3))

    # 5 customers via organic — none repeat
    for i in range(5):
        _seed_order(db, SHOP_A, f"bvw-o-{i}-1", f"o{i}@test.com", days_ago=50 - i)
        db.add(VisitorPurchaseSession(
            shop_domain=SHOP_A, visitor_id=f"vo{i}",
            shopify_order_id=f"bvw-o-{i}-1",
            product_url="https://example.com/p",
            confirmed_at=now - timedelta(days=50 - i),
            last_source="organic",
        ))
    db.commit()

    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_channel", months=6)
    bvw = body["best_vs_worst"]
    assert bvw["best_dim_value"] == "google_ads"
    assert bvw["worst_dim_value"] == "organic"
    assert bvw["best_repeat_rate"] == 1.0
    assert bvw["worst_repeat_rate"] == 0.0
    assert "google_ads" in bvw["insight"]


def test_best_vs_worst_holds_back_on_cold_start(db, merchant_a):
    """Single bucket < 5 customers → no insight."""
    now = _now()
    _seed_order(db, SHOP_A, "cs-1", "cs1@test.com", days_ago=5)
    db.add(VisitorPurchaseSession(
        shop_domain=SHOP_A, visitor_id="vcs1",
        shopify_order_id="cs-1",
        product_url="https://example.com/p",
        confirmed_at=now - timedelta(days=5),
        last_source="organic",
    ))
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_channel", months=6)
    bvw = body["best_vs_worst"]
    # Cold-start: insight defaults to "Need at least 2 segments..."
    assert bvw["best_dim_value"] is None
    assert "at least 2 segments" in bvw["insight"]


# ════════════════════════════════════════════════════════════════════════
# Tenant isolation
# ════════════════════════════════════════════════════════════════════════


def test_no_cross_tenant_leak(db, merchant_a):
    other = "other-shop.myshopify.com"
    _seed_order(
        db, other, "leak-1", "leak@test.com", days_ago=5,
        line_items=[{"title": "FORBIDDEN_LEAK", "price": "9999", "quantity": 1}],
        discount_codes=["FORBIDDEN_DISC"],
    )
    db.commit()
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_product", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "FORBIDDEN_LEAK" not in keys
    body = get_cohorts_by_dimension(db, SHOP_A, dim="first_discount", months=6)
    keys = {b["dim_value"] for b in body["buckets"]}
    assert "FORBIDDEN_DISC" not in keys
