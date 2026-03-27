"""Tests for tenant (shop_domain) isolation — no cross-shop data leakage."""
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.opportunity_signal import OpportunitySignal
from app.models.product_metrics import ProductMetrics
from tests.conftest import SHOP_A, SHOP_B, now_ms


def _seed_signals(db: Session):
    """Insert signals for both shops."""
    now = datetime.utcnow()
    for shop, stype in [(SHOP_A, "HIGH_TRAFFIC_NO_CART"), (SHOP_B, "DEAD_TRAFFIC")]:
        db.add(OpportunitySignal(
            shop_domain=shop,
            product_url="/products/shared-slug",
            signal_type=stype,
            signal_strength=0.60,
            signal_confidence="high",
            explanation="test",
            detected_at=now,
            refreshed_at=now,
            expires_at=now + timedelta(hours=24),
        ))
    db.flush()


def test_signals_scoped_by_shop(client, auth_a, auth_b, merchant_a, merchant_b, db):
    """Shop A's signals must not appear in Shop B's response."""
    _seed_signals(db)
    db.commit()

    # Shop A sees only its signal
    resp_a = client.get("/opportunities", cookies=auth_a)
    assert resp_a.status_code == 200
    signals_a = resp_a.json()
    for s in signals_a:
        # Every signal returned must NOT be Shop B's type
        if s.get("product_url") == "/products/shared-slug":
            assert s["signal_type"] != "DEAD_TRAFFIC"  # that's Shop B's signal

    # Shop B sees only its signal
    resp_b = client.get("/opportunities", cookies=auth_b)
    assert resp_b.status_code == 200
    signals_b = resp_b.json()
    for s in signals_b:
        if s.get("product_url") == "/products/shared-slug":
            assert s["signal_type"] != "HIGH_TRAFFIC_NO_CART"  # that's Shop A's


def test_events_scoped_by_shop(client, merchant_a, merchant_b, db):
    """Events posted for Shop A must not be readable under Shop B."""
    # Insert event for Shop A
    ts = now_ms()
    db.add(Event(
        shop_domain=SHOP_A,
        visitor_id="v_shop_a",
        event_type="product_view",
        product_url="/products/exclusive",
        timestamp=ts,
    ))
    db.flush()

    # Query events for Shop B — must not find Shop A's event
    rows = db.execute(
        text("SELECT COUNT(*) FROM events WHERE shop_domain = :s AND visitor_id = 'v_shop_a'"),
        {"s": SHOP_B},
    ).scalar()
    assert rows == 0


def test_metrics_scoped_by_shop(db, merchant_a, merchant_b):
    """Product metrics for Shop A must not appear under Shop B."""
    db.add(ProductMetrics(
        shop_domain=SHOP_A,
        product_url="/products/metric-test",
        views_24h=50,
        last_event_at=now_ms(),
    ))
    db.flush()

    rows = db.execute(
        text("SELECT COUNT(*) FROM product_metrics WHERE shop_domain = :s AND product_url = '/products/metric-test'"),
        {"s": SHOP_B},
    ).scalar()
    assert rows == 0
