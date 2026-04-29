"""Lite-tier access to /pro/costs/* endpoints — G5 parity gap close
(2026-04-29). OrderMetrics $59, TrueProfit $25, BeProfit, Lifetimely
Free all ship COGS management at lower tiers — flipped from
require_pro_session → require_merchant_session per strict $0-60
parity rule + `feedback_settings_is_tier_agnostic_chrome.md`."""
from __future__ import annotations

from tests.conftest import SHOP_A, SHOP_B, auth_cookies


def test_costs_products_get_lite_returns_200(client, merchant_b, auth_b):
    """GET /pro/costs/products — Lite plan accepted."""
    r = client.get("/pro/costs/products", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shop_domain"] == SHOP_B
    assert "products" in body
    assert isinstance(body["products"], list)


def test_costs_products_get_pro_returns_200(client, merchant_a, auth_a):
    r = client.get("/pro/costs/products", cookies=auth_a)
    assert r.status_code == 200, r.text


def test_costs_products_get_unauthenticated_returns_401(client):
    r = client.get("/pro/costs/products")
    assert r.status_code == 401


def test_costs_products_bulk_upsert_lite_accepts(client, merchant_b, auth_b):
    """POST /pro/costs/products — Lite tier can bulk-upload (CSV import
    flow consumes this). Single-product happy path."""
    payload = {
        "products": [
            {
                "product_key": "shopify-prod-1",
                "product_title": "Test product",
                "cogs_per_unit": 5.50,
                "shipping_cost_per_unit": 1.20,
                "currency": "USD",
            },
        ],
    }
    r = client.post(
        "/pro/costs/products",
        cookies=auth_b,
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] + body["updated"] == 1


def test_costs_products_bulk_upsert_lite_handles_multi_row(client, merchant_b, auth_b):
    """Bulk-upsert must handle the typical CSV size (10s of rows)."""
    payload = {
        "products": [
            {"product_key": f"shopify-prod-{i}", "cogs_per_unit": float(i + 1)}
            for i in range(10)
        ],
    }
    r = client.post(
        "/pro/costs/products",
        cookies=auth_b,
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 10


def test_costs_sync_shopify_lite_returns_200_or_502(client, merchant_b, auth_b):
    """POST /pro/costs/sync-from-shopify — Lite tier reaches the gate.
    Returns 200 (with no_token / shopify_error status) or 502 if
    Shopify Admin API unreachable in test env. Either way, NOT 403."""
    r = client.post(
        "/pro/costs/sync-from-shopify",
        cookies=auth_b,
        headers={"Content-Type": "application/json"},
    )
    # Lite-tier must NOT be 403-ed. 200 with status="no_token" or
    # similar is the typical fresh-shop path.
    assert r.status_code != 403, (
        f"Lite tier unexpectedly forbidden (G5 regression): {r.text}"
    )
    assert r.status_code in (200, 502), r.text


def test_costs_tenant_isolation(client, merchant_a, merchant_b, auth_a, auth_b, db):
    """Shop A's cost rows must not appear in Shop B's response."""
    from app.models.product_cost import ProductCost
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(ProductCost(
        shop_domain=SHOP_A, product_key="shopify-a-1",
        cogs_per_unit=10.0, currency="USD",
        source="manual", created_at=now, updated_at=now,
    ))
    db.add(ProductCost(
        shop_domain=SHOP_B, product_key="shopify-b-1",
        cogs_per_unit=20.0, currency="USD",
        source="manual", created_at=now, updated_at=now,
    ))
    db.flush()

    ra = client.get("/pro/costs/products", cookies=auth_a)
    rb = client.get("/pro/costs/products", cookies=auth_b)
    assert ra.status_code == rb.status_code == 200
    a_keys = {p["product_key"] for p in ra.json()["products"]}
    b_keys = {p["product_key"] for p in rb.json()["products"]}
    assert "shopify-a-1" in a_keys
    assert "shopify-a-1" not in b_keys
    assert "shopify-b-1" in b_keys
    assert "shopify-b-1" not in a_keys
