"""Tests for /merchant/inventory/* — Gap #4 Inventory KPIs."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.models.inventory_snapshot import InventorySnapshot
from app.models.shop_order import ShopOrder
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def _today():
    return datetime.now(timezone.utc).date()


def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_snapshot(db, shop, product_url, title, qty, days_ago=0, variant_id=""):
    db.add(InventorySnapshot(
        shop_domain=shop,
        product_url=product_url,
        product_title=title,
        variant_id=variant_id,
        inventory_quantity=qty,
        snapshot_date=_today() - timedelta(days=days_ago),
    ))


def _seed_order(db, shop, title, qty=1, days_ago=0, price=100.0):
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"o-inv-{title}-{days_ago}-{qty}",
        total_price=price,
        currency="EUR",
        customer_email=f"c{days_ago}@x.com",
        line_items=[{"price": str(price), "quantity": qty, "title": title}],
        created_at=_now_naive() - timedelta(days=days_ago),
        source="webhook",
    ))


# ════════════════════════════════════════════════════════════════════════
# /merchant/inventory/kpis
# ════════════════════════════════════════════════════════════════════════


def test_kpis_empty_state(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/kpis", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["products_tracked"] == 0
    assert body["out_of_stock_count"] == 0
    assert body["low_stock_count"] == 0
    assert body["top_at_risk"] == []
    assert "listening" in body["headline"].lower()


def test_kpis_populated_with_mixed_stock(client, merchant_a, db):
    # 3 products: out-of-stock / low-stock / healthy
    _seed_snapshot(db, SHOP_A, "/products/oos", "OutOfStock", qty=0)
    _seed_snapshot(db, SHOP_A, "/products/low", "LowStock", qty=10)
    _seed_snapshot(db, SHOP_A, "/products/ok", "HealthyStock", qty=200)
    # Sales rates: low has 1/day → 10 days of cover (≤ default 14 lead) → low_stock
    for d in range(30):
        _seed_order(db, SHOP_A, "LowStock", qty=1, days_ago=d)
    # Healthy has 1/day too but qty 200 → 200 days → not low_stock
    for d in range(30):
        _seed_order(db, SHOP_A, "HealthyStock", qty=1, days_ago=d)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/kpis", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["products_tracked"] == 3
    assert body["out_of_stock_count"] == 1
    assert body["low_stock_count"] == 1     # LowStock only
    assert body["lead_time_days"] == 14     # default
    # top_at_risk lists products with qty>0, ordered by days_of_cover ascending
    titles = [r["product_title"] for r in body["top_at_risk"]]
    assert "LowStock" in titles
    assert "OutOfStock" not in titles  # excluded (qty=0)
    assert "reorder soon" in body["headline"].lower()


def test_kpis_lead_time_override(client, merchant_a, db):
    # Set merchant.inventory_lead_time_days=30 → LowStock (10d cover) is now <30d → low_stock
    db.execute(
        ShopOrder.__table__.metadata.tables["merchants"].update().where(
            ShopOrder.__table__.metadata.tables["merchants"].c.shop_domain == SHOP_A
        ).values(inventory_lead_time_days=30)
    )
    _seed_snapshot(db, SHOP_A, "/products/low30", "LowStock30", qty=15)
    for d in range(30):
        _seed_order(db, SHOP_A, "LowStock30", qty=1, days_ago=d)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/kpis", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["lead_time_days"] == 30


def test_kpis_tenant_isolation(client, merchant_a, merchant_b, db):
    _seed_snapshot(db, SHOP_A, "/products/a", "ProductA", qty=5)
    _seed_snapshot(db, SHOP_B, "/products/b", "ProductB", qty=10)
    db.flush()

    body_a = client.get("/merchant/inventory/kpis", cookies=auth_cookies(SHOP_A)).json()
    body_b = client.get("/merchant/inventory/kpis", cookies=auth_cookies(SHOP_B)).json()
    assert body_a["products_tracked"] == 1
    assert body_b["products_tracked"] == 1
    titles_a = {r["product_title"] for r in body_a["top_at_risk"]}
    titles_b = {r["product_title"] for r in body_b["top_at_risk"]}
    assert "ProductB" not in titles_a
    assert "ProductA" not in titles_b


def test_kpis_no_session_returns_401(client):
    r = client.get("/merchant/inventory/kpis")
    assert r.status_code == 401


# ════════════════════════════════════════════════════════════════════════
# /merchant/inventory/details
# ════════════════════════════════════════════════════════════════════════


def test_details_pagination_and_sort(client, merchant_a, db):
    # 5 products, varying days_of_cover
    for i in range(5):
        _seed_snapshot(db, SHOP_A, f"/products/p{i}", f"P{i}", qty=10 * (i + 1))
        for d in range(30):
            _seed_order(db, SHOP_A, f"P{i}", qty=1, days_ago=d)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/details?page=1&page_size=3", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert body["page_size"] == 3
    assert len(body["rows"]) == 3
    # Sorted ascending by days_of_cover → P0 (10 days) first
    assert body["rows"][0]["product_title"] == "P0"
    assert body["rows"][0]["sales_rate_per_day"] == 1.0


def test_details_page_size_capped(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/details?page_size=500", cookies=cookies)
    assert r.status_code == 422  # Pydantic le=100 cap


def test_details_no_recent_sales_renders_no_recent_sales_hint(client, merchant_a, db):
    _seed_snapshot(db, SHOP_A, "/products/dead", "DeadStock", qty=100)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/details", cookies=cookies)
    body = r.json()
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["sales_rate_per_day"] == 0.0
    assert row["days_of_cover"] is None
    assert row["reorder_hint"] == "No recent sales"


# ════════════════════════════════════════════════════════════════════════
# /merchant/inventory/snapshot-status
# ════════════════════════════════════════════════════════════════════════


def test_snapshot_status_fresh(client, merchant_a, db):
    _seed_snapshot(db, SHOP_A, "/products/x", "X", qty=5)
    db.flush()

    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/snapshot-status", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["products_tracked"] == 1
    assert body["last_snapshot_at"] is not None
    assert body["is_fresh"] is True


def test_snapshot_status_empty_returns_not_fresh(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    r = client.get("/merchant/inventory/snapshot-status", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["products_tracked"] == 0
    assert body["last_snapshot_at"] is None
    assert body["is_fresh"] is False
