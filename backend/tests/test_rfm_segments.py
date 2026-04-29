"""RFM segmentation — G2 Lite parity gap close (2026-04-29).

Endpoint: GET /analytics/rfm/segments. Putler $20, Glew (free), Mipler
ship 11-named-segment RFM at entry; HedgeSpark Lite €39 matches.

Coverage:
  * Lite tier (starter plan) gets 200
  * Pro tier gets 200
  * Unauth = 401
  * Empty shop returns total_customers=0 with empty segments[]
  * Populated shop returns segments with valid shape
  * Segment names are from the canonical 11-name set
  * Sample customers use cust_<hash> non-PII IDs (no raw emails)
  * Tenant isolation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.shop_order import ShopOrder
from app.services.rfm import SEGMENT_ORDER, compute_rfm_segments
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_order(db, *, shop, email, total, days_ago, idx, currency="USD"):
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"o-rfm-{shop}-{email}-{idx}",
        total_price=total,
        currency=currency,
        customer_email=email,
        line_items=[{"price": str(total), "quantity": 1, "title": "x"}],
        created_at=_now_naive() - timedelta(days=days_ago),
        source="webhook",
    ))


def test_rfm_lite_returns_200(client, merchant_b, auth_b):
    r = client.get("/analytics/rfm/segments", cookies=auth_b)
    assert r.status_code == 200, r.text


def test_rfm_pro_returns_200(client, merchant_a, auth_a):
    r = client.get("/analytics/rfm/segments", cookies=auth_a)
    assert r.status_code == 200, r.text


def test_rfm_unauth_returns_401(client):
    r = client.get("/analytics/rfm/segments")
    assert r.status_code == 401


def test_rfm_empty_shop_returns_zero(client, merchant_b, auth_b):
    """Shop with no orders → total_customers=0 + empty segments."""
    r = client.get("/analytics/rfm/segments", cookies=auth_b)
    assert r.status_code == 200
    body = r.json()
    assert body["total_customers"] == 0
    assert body["segments"] == []


def test_rfm_populated_shop_returns_segments(merchant_a, db):
    """Seed a small but realistic customer base; expect at least one
    segment populated and every name from the canonical set."""
    # 3 customers with varied R/F/M profiles. Currency must match
    # merchant_a's primary_currency (USD/null) — service filters by it.
    _seed_order(db, shop=SHOP_A, email="champ@x.com", total=100.0, days_ago=2, idx=1)
    _seed_order(db, shop=SHOP_A, email="champ@x.com", total=150.0, days_ago=5, idx=2)
    _seed_order(db, shop=SHOP_A, email="champ@x.com", total=200.0, days_ago=10, idx=3)
    _seed_order(db, shop=SHOP_A, email="lost@x.com", total=20.0, days_ago=400, idx=1)
    _seed_order(db, shop=SHOP_A, email="newby@x.com", total=50.0, days_ago=1, idx=1)
    db.flush()

    result = compute_rfm_segments(db, SHOP_A)
    assert result["total_customers"] == 3
    assert len(result["segments"]) >= 1
    for seg in result["segments"]:
        assert seg["name"] in SEGMENT_ORDER, f"unknown segment {seg['name']}"
        assert seg["count"] >= 1
        assert seg["share_pct"] >= 0
        assert "description" in seg
        assert isinstance(seg["sample_customers"], list)


def test_rfm_sample_customers_are_pii_safe(merchant_a, db):
    """sample_customers IDs must be cust_<8hex> form, never raw email."""
    _seed_order(db, shop=SHOP_A, email="rawpii@example.com", total=100.0, days_ago=1, idx=1)
    db.flush()

    result = compute_rfm_segments(db, SHOP_A)
    leaked = []
    for seg in result["segments"]:
        for c in seg["sample_customers"]:
            cid = c["id"]
            if "@" in cid or "rawpii" in cid:
                leaked.append(cid)
            assert cid.startswith("cust_"), f"non-canonical ID: {cid}"
    assert leaked == [], f"PII leaked: {leaked}"


def test_rfm_tenant_isolation(merchant_a, merchant_b, db):
    """Shop A's customers must not appear in Shop B's segmentation."""
    _seed_order(db, shop=SHOP_A, email="a-only@x.com", total=100.0, days_ago=1, idx=1)
    _seed_order(db, shop=SHOP_B, email="b-only@x.com", total=200.0, days_ago=1, idx=1)
    db.flush()

    a = compute_rfm_segments(db, SHOP_A)
    b = compute_rfm_segments(db, SHOP_B)
    a_total = sum(s["count"] for s in a["segments"])
    b_total = sum(s["count"] for s in b["segments"])
    assert a_total == 1
    assert b_total == 1


def test_rfm_currency_filter_excludes_foreign_orders(merchant_eur, db):
    """An EUR shop's RFM excludes USD orders (multi-store / mis-tagged)."""
    SHOP = "test-shop-eur.myshopify.com"
    _seed_order(db, shop=SHOP, email="eur-buyer@x.com", total=100.0, days_ago=1, idx=1, currency="EUR")
    _seed_order(db, shop=SHOP, email="usd-buyer@x.com", total=200.0, days_ago=1, idx=1, currency="USD")
    db.flush()

    result = compute_rfm_segments(db, SHOP)
    # Only the EUR buyer counts (currency must match shop primary).
    assert result["currency"] == "EUR"
    assert result["total_customers"] == 1
