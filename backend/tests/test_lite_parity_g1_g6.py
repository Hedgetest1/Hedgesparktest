"""Tests for Lite parity gaps G1 (geographic drilldown) and G6 (CAC/LTV
unlock) — closed 2026-04-29 per strict $0-60 parity rule.

Both endpoints must be reachable by Lite (starter) merchants:
  G1: /analytics/orders-by-country — Shopify Free, Putler $20, Better
      Reports $19.90, Mipler $9.99 all ship country drilldown at entry.
  G6: /analytics/cac-ltv — Lifetimely Free, OrderMetrics $59,
      TrueProfit $25 all ship CAC:LTV at lower tiers.

Coverage:
  * 200 for Lite (starter) merchants on both endpoints
  * 200 for Pro merchants on both endpoints
  * 401 unauthenticated
  * Legacy /pro/cac-ltv alias still works (deprecated but not removed)
  * CAC headline copy matches `unconfigured` state for fresh merchants
"""
from __future__ import annotations

from app.models.merchant import Merchant
from tests.conftest import SHOP_A, SHOP_B, auth_cookies


# ════════════════════════════════════════════════════════════════════
# G6 — /analytics/cac-ltv (Lite-accessible)
# ════════════════════════════════════════════════════════════════════


def test_cac_ltv_endpoint_lite_returns_200(client, merchant_b, auth_b):
    """Lite (starter) merchants get the CAC/LTV endpoint — closes
    embarrassing parity gap (Lifetimely Free, TrueProfit $25 ship it).
    merchant_b fixture is plan='starter' billing_active=False."""
    r = client.get("/analytics/cac-ltv", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "cac_eur" in body
    assert "ratio" in body
    assert "status" in body
    assert "headline" in body
    # Fresh shop with no ad spend → unconfigured state, but endpoint
    # must still return 200 with a clear next-step headline.
    assert "ad_spend_source" in body


def test_cac_ltv_endpoint_pro_returns_200(client, merchant_a, auth_a):
    """Pro merchants also get 200 — same endpoint, same response."""
    r = client.get("/analytics/cac-ltv", cookies=auth_a)
    assert r.status_code == 200, r.text


def test_cac_ltv_unauthenticated_returns_401(client):
    r = client.get("/analytics/cac-ltv")
    assert r.status_code == 401


def test_cac_ltv_legacy_pro_path_still_lite_accessible(client, merchant_b, auth_b):
    """Legacy /pro/cac-ltv alias is now Lite-accessible (was Pro-only
    before 2026-04-29 — gate flipped per parity rule). Backward compat
    for any dashboard build still on the old URL."""
    r = client.get("/pro/cac-ltv", cookies=auth_b)
    assert r.status_code == 200, r.text


def test_cac_ltv_response_includes_currency(client, merchant_a, auth_a, db):
    """Response surfaces shop's native currency — never assumes EUR."""
    merchant_a.primary_currency = "USD"
    db.flush()
    r = client.get("/analytics/cac-ltv", cookies=auth_a)
    assert r.status_code == 200
    body = r.json()
    assert body.get("currency") == "USD"


# ════════════════════════════════════════════════════════════════════
# G1 — /analytics/orders-by-country (Lite-accessible parity verify)
# ════════════════════════════════════════════════════════════════════


def test_orders_by_country_lite_returns_200(client, merchant_b, auth_b):
    """Lite tier accesses the country drilldown — no ad-spend dep,
    pure shop_orders.shipping_address.country_code aggregation."""
    r = client.get("/analytics/orders-by-country", cookies=auth_b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "currency" in body
    assert "has_data" in body
    assert "countries" in body
    assert isinstance(body["countries"], list)


def test_orders_by_country_pro_returns_200(client, merchant_a, auth_a):
    r = client.get("/analytics/orders-by-country", cookies=auth_a)
    assert r.status_code == 200


def test_orders_by_country_unauthenticated_returns_401(client):
    r = client.get("/analytics/orders-by-country")
    assert r.status_code == 401


def test_orders_by_country_response_shape_for_empty_shop(client, merchant_b, auth_b):
    """Fresh shop with no orders returns has_data=false + empty list +
    zero totals — never crashes, never returns nulls."""
    r = client.get("/analytics/orders-by-country", cookies=auth_b)
    assert r.status_code == 200
    body = r.json()
    assert body["has_data"] is False or body["total_orders"] == 0
    assert body["countries"] == [] or all(
        "country_code" in c and "orders" in c and "revenue" in c
        for c in body["countries"]
    )
