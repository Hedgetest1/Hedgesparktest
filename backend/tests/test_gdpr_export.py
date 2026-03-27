"""Tests for GDPR customers/data_request export pipeline."""
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.gdpr_request import GdprRequest
from app.models.merchant import Merchant
from app.services.gdpr_processor import process_gdpr_request
from tests.conftest import SHOP_A, now_ms


def _seed_customer_with_data(db: Session):
    """Seed a merchant, order, visitor bridge, events, and visitor state."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    # Events
    for i in range(3):
        db.add(Event(
            shop_domain=SHOP_A, visitor_id="export_vid_1",
            event_type="product_view", product_url="/products/exported-item",
            timestamp=now_ms() - i * 60000,
        ))

    # Order
    db.execute(text("""
        INSERT INTO shop_orders (shop_domain, shopify_order_id, total_price, currency,
            customer_id, customer_email, line_items, created_at, ingested_at, source)
        VALUES (:shop, '88001', 29.99, 'EUR', '555', 'export@test.com', '[]', NOW(), NOW(), 'webhook')
    """), {"shop": SHOP_A})

    # Visitor bridge
    db.execute(text("""
        INSERT INTO visitor_purchase_sessions (shop_domain, visitor_id, shopify_order_id, confirmed_at, ingested_at)
        VALUES (:shop, 'export_vid_1', '88001', NOW(), NOW())
    """), {"shop": SHOP_A})

    db.execute(text("""
        INSERT INTO visitors (visitor_id, shop_domain, first_seen, last_seen)
        VALUES ('export_vid_1', :shop, NOW(), NOW())
    """), {"shop": SHOP_A})

    db.flush()


def test_data_request_produces_export(db):
    """customers_data_request must produce a structured JSON export."""
    _seed_customer_with_data(db)

    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="555",
        customer_email="export@test.com",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    assert req.status == "completed"
    export = json.loads(req.result_summary)
    assert export["shop_domain"] == SHOP_A
    assert export["customer_id"] == "555"
    assert export["data"]["visitor_ids_found"] >= 1
    assert len(export["data"]["orders"]) >= 1
    assert export["data"]["orders"][0]["order_id"] == "88001"


def test_data_request_includes_events(db):
    """Export must include behavioral events for identified visitors."""
    _seed_customer_with_data(db)

    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="555",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    export = json.loads(req.result_summary)
    assert len(export["data"].get("events", [])) >= 3


def test_data_request_no_customer_data(db):
    """Export for unknown customer produces empty but valid response."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="nonexistent",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    assert req.status == "completed"
    export = json.loads(req.result_summary)
    assert export["data"]["visitor_ids_found"] == 0
