"""
Tests for /analytics/today-snapshot — Lite-floor day-1 base analytics.

Born 2026-04-25 to close the audit gap exposed when Lite shipped the
intelligence layer (RARS / peers / P&L / cassettoni) without grounding
the merchant in the basic "today vs yesterday" pulse every cheap
Shopify analytics tool surfaces by default.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.models.shop_order import ShopOrder
from tests.conftest import SHOP_A, auth_cookies


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_orders(db, shop: str, currency: str = "USD") -> None:
    """Seed today + yesterday orders, plus old history for new-vs-returning."""
    today = _utc_now()
    yesterday = today - timedelta(days=1)
    last_month = today - timedelta(days=45)

    # Last month — establishes "returning customer" pool
    for i, email in enumerate(["alice@x.com", "bob@x.com", "carol@x.com"]):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"hist-{i}",
            total_price=50.00,
            currency=currency,
            customer_email=email,
            line_items=[{"title": "Widget", "price": "50.00", "quantity": 1}],
            created_at=last_month,
            source="webhook",
        ))

    # Yesterday — 2 orders, AOV $75
    for i, (email, amt) in enumerate([("alice@x.com", 60.00), ("dave@x.com", 90.00)]):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"yest-{i}",
            total_price=amt,
            currency=currency,
            customer_email=email,
            line_items=[{"title": "Widget", "price": str(amt), "quantity": 1}],
            created_at=yesterday,
            source="webhook",
        ))

    # Today — 3 orders, AOV $100
    for i, (email, amt, item) in enumerate([
        ("bob@x.com", 80.00, "Silk Pillow"),       # returning
        ("eve@x.com", 100.00, "Ceramic Mug"),      # NEW (no history)
        ("alice@x.com", 120.00, "Silk Pillow"),    # returning, top-seller boost
    ]):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"today-{i}",
            total_price=amt,
            currency=currency,
            customer_email=email,
            line_items=[{"title": item, "price": str(amt), "quantity": 1}],
            created_at=today,
            source="webhook",
        ))
    db.flush()


def _seed_sessions(db, shop: str) -> None:
    """Seed page_view events for sessions counting."""
    today_ms = int(_utc_now().replace(hour=10, minute=0, second=0, microsecond=0).timestamp() * 1000)
    yest_ms = int((_utc_now() - timedelta(days=1)).replace(hour=10).timestamp() * 1000)

    # Today: 100 distinct visitors
    for i in range(100):
        db.add(Event(
            visitor_id=f"v-today-{i}",
            event_type="page_view",
            shop_domain=shop,
            timestamp=today_ms + i * 1000,
            url="/",
        ))
    # Yesterday: 80 distinct visitors
    for i in range(80):
        db.add(Event(
            visitor_id=f"v-yest-{i}",
            event_type="page_view",
            shop_domain=shop,
            timestamp=yest_ms + i * 1000,
            url="/",
        ))
    db.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_today_snapshot_requires_auth(client):
    """Unauthenticated request must return 401/403."""
    r = client.get("/analytics/today-snapshot")
    assert r.status_code in (401, 403)


def test_today_snapshot_empty_shop(client, merchant_a, auth_a):
    """Brand-new merchant with zero orders + zero sessions → has_data=False."""
    r = client.get("/analytics/today-snapshot", cookies=auth_a)
    assert r.status_code == 200
    j = r.json()
    assert j["has_data"] is False
    assert j["today"]["revenue"] == 0
    assert j["today"]["orders"] == 0
    assert j["today"]["aov"] == 0
    assert j["today"]["sessions"] == 0
    assert j["today"]["conversion_rate_pct"] is None
    assert j["yesterday"]["conversion_rate_pct"] is None
    # Deltas null when both sides are zero — no fabricated +∞%
    assert j["deltas"]["revenue_pct"] is None
    assert j["deltas"]["orders_pct"] is None
    assert j["deltas"]["sessions_pct"] is None
    assert j["top_sellers_today"] == []


def test_today_snapshot_real_data(client, merchant_a, auth_a, db):
    """Seeded today + yesterday orders + sessions → correct math, correct
    delta direction, top-sellers ranked by revenue, new-vs-returning split."""
    _seed_orders(db, SHOP_A, currency="USD")
    _seed_sessions(db, SHOP_A)

    r = client.get("/analytics/today-snapshot", cookies=auth_a)
    assert r.status_code == 200
    j = r.json()

    assert j["has_data"] is True
    assert j["currency"] == "USD"

    # Today: 3 orders, total 80+100+120=300, AOV 100
    assert j["today"]["orders"] == 3
    assert abs(j["today"]["revenue"] - 300.00) < 0.01
    assert abs(j["today"]["aov"] - 100.00) < 0.01

    # Yesterday: 2 orders, total 150, AOV 75
    assert j["yesterday"]["orders"] == 2
    assert abs(j["yesterday"]["revenue"] - 150.00) < 0.01
    assert abs(j["yesterday"]["aov"] - 75.00) < 0.01

    # Sessions
    assert j["today"]["sessions"] == 100
    assert j["yesterday"]["sessions"] == 80

    # Conversion: 3/100 = 3.0%, 2/80 = 2.5%
    assert abs(j["today"]["conversion_rate_pct"] - 3.0) < 0.01
    assert abs(j["yesterday"]["conversion_rate_pct"] - 2.5) < 0.01

    # New vs returning today: only eve@x.com is brand new (no prior order)
    assert j["today"]["new_customers"] == 1
    # alice + bob both have prior orders today
    assert j["today"]["returning_customers"] == 2

    # Deltas: revenue +100% (300 vs 150), orders +50%, sessions +25%
    assert abs(j["deltas"]["revenue_pct"] - 100.0) < 0.1
    assert abs(j["deltas"]["orders_pct"] - 50.0) < 0.1
    assert abs(j["deltas"]["sessions_pct"] - 25.0) < 0.1
    # Conversion delta is points, not percent
    assert abs(j["deltas"]["conversion_rate_pct_delta"] - 0.5) < 0.05

    # Top sellers — Silk Pillow (80+120=200) > Ceramic Mug (100)
    sellers = j["top_sellers_today"]
    assert len(sellers) == 2
    assert sellers[0]["product_title"] == "Silk Pillow"
    assert abs(sellers[0]["revenue"] - 200.00) < 0.01
    assert sellers[0]["units_sold"] == 2
    assert sellers[1]["product_title"] == "Ceramic Mug"


def test_today_snapshot_delta_null_when_yesterday_zero(client, merchant_a, auth_a, db):
    """Today has data, yesterday is empty → deltas must be null, never
    fabricated +∞% or +100%. The audit explicitly forbids that."""
    today = _utc_now()
    db.add(ShopOrder(
        shop_domain=SHOP_A,
        shopify_order_id="today-only",
        total_price=200.00,
        currency="USD",
        customer_email="solo@x.com",
        line_items=[{"title": "Solo", "price": "200.00", "quantity": 1}],
        created_at=today,
        source="webhook",
    ))
    db.flush()

    r = client.get("/analytics/today-snapshot", cookies=auth_a)
    j = r.json()
    assert j["has_data"] is True
    assert j["today"]["orders"] == 1
    assert j["yesterday"]["orders"] == 0
    # The honest answer to "% change from zero" is null, not +∞%
    assert j["deltas"]["revenue_pct"] is None
    assert j["deltas"]["orders_pct"] is None


def test_today_snapshot_currency_isolation(client, merchant_eur, auth_eur, db):
    """EUR-currency shop: orders in USD must be ignored. Currency-correctness
    smoke — guards the cross-currency drift class."""
    today = _utc_now()
    # Real EUR order
    db.add(ShopOrder(
        shop_domain="test-shop-eur.myshopify.com",
        shopify_order_id="eur-1",
        total_price=100.00,
        currency="EUR",
        customer_email="eur@x.com",
        line_items=[{"title": "Eurowidget", "price": "100.00", "quantity": 1}],
        created_at=today,
        source="webhook",
    ))
    # Cross-currency noise — must not be summed into the EUR revenue
    db.add(ShopOrder(
        shop_domain="test-shop-eur.myshopify.com",
        shopify_order_id="usd-noise",
        total_price=999.99,
        currency="USD",
        customer_email="usd@x.com",
        line_items=[{"title": "USDwidget", "price": "999.99", "quantity": 1}],
        created_at=today,
        source="webhook",
    ))
    db.flush()

    r = client.get("/analytics/today-snapshot", cookies=auth_eur)
    j = r.json()
    assert j["currency"] == "EUR"
    assert j["today"]["orders"] == 1
    assert abs(j["today"]["revenue"] - 100.00) < 0.01
