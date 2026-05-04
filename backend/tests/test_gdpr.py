"""Tests for GDPR deletion/redaction pipeline (gdpr_processor.py)."""
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.gdpr_request import GdprRequest
from app.models.merchant import Merchant
from app.services.gdpr_processor import process_gdpr_request
from tests.conftest import SHOP_A, now_ms


def _seed_customer_data(db: Session):
    """Seed events, orders, and visitor bridge for a test customer."""
    # Merchant
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    # Events for the customer's visitor_id
    for i in range(3):
        db.add(Event(
            shop_domain=SHOP_A,
            visitor_id="gdpr_test_visitor",
            event_type="product_view",
            product_url="/products/test",
            timestamp=now_ms() - i * 60000,
        ))

    # Shop order with customer info
    db.execute(text("""
        INSERT INTO shop_orders (shop_domain, shopify_order_id, total_price, currency,
            customer_id, customer_email, line_items, created_at, ingested_at, source)
        VALUES (:shop, '99001', 49.99, 'USD', '12345', 'customer@test.com', '[]', NOW(), NOW(), 'webhook')
    """), {"shop": SHOP_A})

    # Visitor bridge
    db.execute(text("""
        INSERT INTO visitor_purchase_sessions (shop_domain, visitor_id, shopify_order_id, confirmed_at, ingested_at)
        VALUES (:shop, 'gdpr_test_visitor', '99001', NOW(), NOW())
    """), {"shop": SHOP_A})

    # Visitor row
    db.execute(text("""
        INSERT INTO visitors (visitor_id, shop_domain, first_seen, last_seen)
        VALUES ('gdpr_test_visitor', :shop, NOW(), NOW())
    """), {"shop": SHOP_A})

    db.flush()


def test_customers_redact_deletes_events(db):
    """customers_redact must delete events for the identified visitor."""
    _seed_customer_data(db)

    # Verify data exists
    event_count = db.execute(text(
        "SELECT COUNT(*) FROM events WHERE shop_domain = :s AND visitor_id = 'gdpr_test_visitor'"
    ), {"s": SHOP_A}).scalar()
    assert event_count == 3

    # Create and process GDPR request
    req = GdprRequest(
        request_type="customers_redact",
        shop_domain=SHOP_A,
        customer_id="12345",
        customer_email="customer@test.com",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    assert req.status == "completed"
    result = json.loads(req.result_summary)
    assert result["events"] == 3
    assert result["visitors"] >= 1

    # Verify events are gone
    remaining = db.execute(text(
        "SELECT COUNT(*) FROM events WHERE shop_domain = :s AND visitor_id = 'gdpr_test_visitor'"
    ), {"s": SHOP_A}).scalar()
    assert remaining == 0


def test_customers_redact_nullifies_email(db):
    """customers_redact must nullify customer_email in shop_orders, keep financial data."""
    _seed_customer_data(db)

    req = GdprRequest(
        request_type="customers_redact",
        shop_domain=SHOP_A,
        customer_id="12345",
        customer_email="customer@test.com",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    # Email must be nullified
    row = db.execute(text(
        "SELECT customer_email, total_price FROM shop_orders WHERE shop_domain = :s AND shopify_order_id = '99001'"
    ), {"s": SHOP_A}).fetchone()
    assert row[0] is None         # email redacted
    assert float(row[1]) == 49.99  # financial data retained (GDPR Art. 17(3)(b))


def test_shop_redact_deletes_all_data(db):
    """shop_redact must delete ALL data for a shop including the merchant row."""
    _seed_customer_data(db)

    # Verify merchant exists
    m = db.execute(text("SELECT COUNT(*) FROM merchants WHERE shop_domain = :s"), {"s": SHOP_A}).scalar()
    assert m == 1

    req = GdprRequest(
        request_type="shop_redact",
        shop_domain=SHOP_A,
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    # shop_redact deletes the gdpr_requests row itself, so req is gone.
    # Verify via audit log instead.
    audit = db.execute(text(
        "SELECT action_type, after_state FROM audit_log WHERE action_type = 'gdpr_shop_redact' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    result = json.loads(audit[1])

    # Key tables must have been cleaned
    assert result.get("events", 0) >= 3
    assert result.get("merchants", 0) >= 1

    # Verify merchant row is gone
    m = db.execute(text("SELECT COUNT(*) FROM merchants WHERE shop_domain = :s"), {"s": SHOP_A}).scalar()
    assert m == 0


def test_shop_redact_returns_per_table_counts(db):
    """Multi-CTE refactor (2026-05-04 wave 8) must return per-table
    rowcounts in the audit JSON for every table the request touched.
    Regression guard: prior loop-based code returned per-table counts;
    new bulk-CTE must do the same."""
    _seed_customer_data(db)

    # Add rows in additional categories the seed doesn't cover so we
    # can assert per-table counts > 0 across multiple CTE clauses.
    db.execute(text("""
        INSERT INTO opportunity_signals (shop_domain, product_url, signal_type,
            signal_strength, signal_confidence, detected_at, refreshed_at, expires_at)
        VALUES (:s, '/p/x', 'high_intent', 0.9, 'high', NOW(), NOW(),
                NOW() + INTERVAL '7 days')
    """), {"s": SHOP_A})
    db.execute(text("""
        INSERT INTO ops_alerts (shop_domain, alert_type, severity, summary,
            source, resolved, created_at)
        VALUES (:s, 'test', 'info', 'test', 'test', false, NOW())
    """), {"s": SHOP_A})
    db.flush()

    req = GdprRequest(
        request_type="shop_redact",
        shop_domain=SHOP_A,
        status="pending",
    )
    db.add(req)
    db.flush()
    process_gdpr_request(db, req)

    audit = db.execute(text(
        "SELECT after_state FROM audit_log WHERE action_type = 'gdpr_shop_redact' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    result = json.loads(audit[0])

    # Per-table counts present and accurate for the seeded categories
    assert result.get("events") == 3
    assert result.get("shop_orders") == 1
    assert result.get("visitors") == 1
    assert result.get("opportunity_signals") == 1
    assert result.get("ops_alerts") == 1
    assert result.get("merchants") == 1

    # Tables we did NOT seed should report 0 (not missing — the CTE
    # always returns a key for every table)
    assert "active_nudges" in result
    assert result["active_nudges"] == 0


def test_shop_redact_atomic_no_partial_state(db):
    """Multi-CTE atomicity guard: shop_redact must leave NO orphan
    rows under shop_domain after success. Replaces the prior per-table
    rollback pattern that could silently corrupt partial-erasure state."""
    _seed_customer_data(db)

    req = GdprRequest(
        request_type="shop_redact",
        shop_domain=SHOP_A,
        status="pending",
    )
    db.add(req)
    db.flush()
    process_gdpr_request(db, req)

    # Probe a few representative tables that should have zero rows
    # for SHOP_A after redaction.
    for tbl in ("events", "shop_orders", "visitors", "merchants"):
        n = db.execute(text(
            f"SELECT COUNT(*) FROM {tbl} WHERE shop_domain = :s"
        ), {"s": SHOP_A}).scalar()
        assert n == 0, f"{tbl} still has rows for shop after shop_redact"


def test_gdpr_creates_audit_log_entry(db):
    """GDPR processing must write an audit log entry."""
    _seed_customer_data(db)

    req = GdprRequest(
        request_type="customers_redact",
        shop_domain=SHOP_A,
        customer_id="12345",
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    audit = db.execute(text(
        "SELECT action_type, actor_name, status, shop_domain FROM audit_log WHERE shop_domain = :s ORDER BY id DESC LIMIT 1"
    ), {"s": SHOP_A}).fetchone()
    assert audit is not None
    assert audit[0] == "gdpr_customers_redact"
    assert audit[1] == "gdpr_worker"
    assert audit[2] == "completed"
    assert audit[3] == SHOP_A
