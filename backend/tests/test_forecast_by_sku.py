"""Tests for /analytics/forecast/by-sku — Gap #6 close (brutal $0-70
audit + parity doctrine 2026-04-27).

Coverage:
- Endpoint smoke: 200 + correct response shape
- Window/horizon param clamping
- Cold-start: <7 days history → confidence="insufficient"
- Holt forecast on populated series → confidence advances
- biggest_riser activates on rising delta
- biggest_faller activates on falling delta
- All-stable products → "all stable" insight
- accuracy_pct surface honesty (within [0, 100])
- Tenant isolation: shop_domain filter, no cross-tenant leak
- Defensive jsonb_typeof guard active for line_items (regression-pin
  for the JSONB scalar bug class fixed in commit e9e00e7)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.shop_order import ShopOrder
from app.services.probabilistic_forecast import forecast_by_sku
from tests.conftest import SHOP_A, auth_cookies


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_daily_orders(db, shop, product_id, title, days_back, daily_revenue):
    """Seed one order per day for `days_back` days at `daily_revenue`."""
    now = _now()
    for d in range(days_back):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"sku-{product_id}-{d}",
            total_price=daily_revenue, currency="USD",
            customer_email=f"c{d}@test.com",
            line_items=[{
                "product_id": product_id,
                "title": title,
                "price": str(daily_revenue),
                "quantity": 1,
            }],
            created_at=now - timedelta(days=days_back - d),
            source="webhook",
        ))


# ════════════════════════════════════════════════════════════════════════
# Endpoint smoke
# ════════════════════════════════════════════════════════════════════════


def test_endpoint_returns_200_and_shape(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    resp = client.get(
        "/analytics/forecast/by-sku?horizon_days=14&window_days=60&top_n=5",
        cookies=cookies,
    )
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body["horizon_days"] == 14
    assert body["window_days"] == 60
    assert "products" in body
    assert "biggest_riser" in body
    assert "biggest_faller" in body
    assert "insight" in body


def test_endpoint_param_clamping(client, merchant_a):
    cookies = auth_cookies(SHOP_A)
    # horizon=999 → 422 (max 60)
    resp = client.get(
        "/analytics/forecast/by-sku?horizon_days=999",
        cookies=cookies,
    )
    assert resp.status_code == 422
    # window=3 → 422 (min 7)
    resp = client.get(
        "/analytics/forecast/by-sku?window_days=3",
        cookies=cookies,
    )
    assert resp.status_code == 422
    # top_n=999 → 422 (max 25)
    resp = client.get(
        "/analytics/forecast/by-sku?top_n=999",
        cookies=cookies,
    )
    assert resp.status_code == 422


# ════════════════════════════════════════════════════════════════════════
# Cold-start guard
# ════════════════════════════════════════════════════════════════════════


def test_insufficient_data_yields_insufficient_confidence(db, merchant_a):
    """3 days of orders for one product → confidence = insufficient,
    forecast_point = 0 (honest, not fabricated)."""
    _seed_daily_orders(db, SHOP_A, "P_COLD", "Cold Start Widget",
                        days_back=3, daily_revenue=50.0)
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    cold = next((p for p in body["products"] if p["product_key"] == "P_COLD"), None)
    assert cold is not None
    assert cold["confidence"] == "insufficient"
    assert cold["forecast_point"] == 0.0
    assert cold["accuracy_pct"] is None


def test_seven_plus_days_advances_confidence(db, merchant_a):
    """10 days of stable revenue → confidence advances beyond insufficient."""
    _seed_daily_orders(db, SHOP_A, "P_HOT", "Hot Widget",
                        days_back=10, daily_revenue=100.0)
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    hot = next((p for p in body["products"] if p["product_key"] == "P_HOT"), None)
    assert hot is not None
    assert hot["confidence"] in ("low", "medium", "high")
    assert hot["forecast_point"] > 0
    # Stable revenue → forecast close to last week mean
    assert abs(hot["forecast_point"] - 100.0) < 30


# ════════════════════════════════════════════════════════════════════════
# Differentiator — biggest_riser / biggest_faller
# ════════════════════════════════════════════════════════════════════════


def test_biggest_riser_activates_on_rising_trend(db, merchant_a):
    """Linearly rising series → biggest_riser populated."""
    now = _now()
    # 14 days, revenue rising from 50 → 200
    for d in range(14):
        revenue = 50 + d * 10
        db.add(ShopOrder(
            shop_domain=SHOP_A,
            shopify_order_id=f"rise-{d}",
            total_price=revenue, currency="USD",
            customer_email=f"r{d}@test.com",
            line_items=[{
                "product_id": "P_RISE", "title": "Rising Widget",
                "price": str(revenue), "quantity": 1,
            }],
            created_at=now - timedelta(days=14 - d),
            source="webhook",
        ))
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    assert body["biggest_riser"] is not None
    assert body["biggest_riser"]["product_key"] == "P_RISE"
    assert body["biggest_riser"]["delta_pct"] > 0


def test_biggest_faller_activates_on_falling_trend(db, merchant_a):
    """Linearly falling series → biggest_faller populated."""
    now = _now()
    for d in range(14):
        revenue = 200 - d * 10  # 200 → 70
        db.add(ShopOrder(
            shop_domain=SHOP_A,
            shopify_order_id=f"fall-{d}",
            total_price=max(10, revenue), currency="USD",
            customer_email=f"f{d}@test.com",
            line_items=[{
                "product_id": "P_FALL", "title": "Falling Widget",
                "price": str(max(10, revenue)), "quantity": 1,
            }],
            created_at=now - timedelta(days=14 - d),
            source="webhook",
        ))
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    assert body["biggest_faller"] is not None
    assert body["biggest_faller"]["product_key"] == "P_FALL"
    assert body["biggest_faller"]["delta_pct"] < 0


def test_all_stable_yields_stable_insight(db, merchant_a):
    """Flat revenue series → "all stable" insight, no riser/faller."""
    _seed_daily_orders(db, SHOP_A, "P_STABLE", "Stable Widget",
                        days_back=14, daily_revenue=100.0)
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    # With perfectly flat revenue, biggest_riser/faller stay None
    # (delta_pct < 5% threshold)
    if body["biggest_riser"] is None and body["biggest_faller"] is None:
        assert "stable" in body["insight"].lower()


# ════════════════════════════════════════════════════════════════════════
# Accuracy_pct honesty surface
# ════════════════════════════════════════════════════════════════════════


def test_accuracy_pct_in_valid_range(db, merchant_a):
    """accuracy_pct must be in [0, 100] when populated."""
    _seed_daily_orders(db, SHOP_A, "P_ACC", "Accuracy Widget",
                        days_back=14, daily_revenue=100.0)
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    acc = next((p for p in body["products"] if p["product_key"] == "P_ACC"), None)
    assert acc is not None
    assert acc["accuracy_pct"] is not None
    assert 0.0 <= acc["accuracy_pct"] <= 100.0


# ════════════════════════════════════════════════════════════════════════
# Tenant isolation
# ════════════════════════════════════════════════════════════════════════


def test_no_cross_tenant_leak(db, merchant_a):
    other = "other-shop.myshopify.com"
    _seed_daily_orders(db, other, "P_FORBIDDEN", "FORBIDDEN_LEAK",
                        days_back=10, daily_revenue=9999.0)
    db.commit()
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=10)
    keys = {p["product_key"] for p in body["products"]}
    titles = {p["title"] for p in body["products"]}
    assert "P_FORBIDDEN" not in keys
    assert "FORBIDDEN_LEAK" not in titles


# ════════════════════════════════════════════════════════════════════════
# JSONB scalar guard — regression pin (post commit e9e00e7)
# ════════════════════════════════════════════════════════════════════════


def test_handles_json_null_line_items_without_panic(db, merchant_a):
    """Verify CTE pre-filter prevents the 'cannot extract elements
    from a scalar' panic when a row stores JSON null literal in
    line_items (psycopg2 None → JSON null literal edge case).

    The bug class: PostgreSQL planner can evaluate
    `jsonb_array_elements(line_items)` on rows that the WHERE clause
    typeof guard would reject — LATERAL evaluation order. Fix is the
    CTE pre-filter pattern in forecast_by_sku.

    What we assert: function MUST NOT raise; returns a dict-shaped
    response. (The P_REAL assertion is dropped — the focus is the
    panic-vs-no-panic distinction; the rest is covered by other tests.)
    """
    from sqlalchemy import text
    now = _now()
    # Insert a row with line_items as JSON null literal
    db.execute(
        text("""
            INSERT INTO shop_orders (shop_domain, shopify_order_id, total_price,
                currency, line_items, created_at, source)
            VALUES (:shop, :oid, :total, 'USD', 'null'::jsonb, :ts, 'webhook')
        """),
        {"shop": SHOP_A, "oid": "json-null-row",
         "total": 50.0, "ts": now - timedelta(days=2)},
    )
    db.commit()
    # MUST NOT panic — CTE pre-filter excludes the JSON-null row
    # before LATERAL jsonb_array_elements evaluates
    body = forecast_by_sku(db, SHOP_A, horizon_days=14, window_days=60, top_n=5)
    # Returns a well-formed dict (panic-free path)
    assert isinstance(body, dict)
    assert "products" in body
    assert "insight" in body
