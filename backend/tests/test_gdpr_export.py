"""Tests for GDPR customers/data_request export pipeline.

Updated 2026-05-14 (TIER_2 refactor): result_summary now stores a
RECEIPT (counts + delivery status + recipient hash), not the raw
PII export blob. Email delivery is mandatory — no customer_email
or orchestrator failure → status='failed', operator retries via
POST /ops/gdpr/exports/{id}/redeliver. The export itself lives
in-memory only during the worker call.
"""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.event import Event
from app.models.gdpr_request import GdprRequest
from app.models.merchant import Merchant
from app.services.gdpr_processor import (
    GdprDeliveryFailed,
    _build_export_receipt,
    process_gdpr_request,
)
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


def test_data_request_persists_receipt_only(db):
    """customers_data_request: on success, result_summary contains a
    RECEIPT (counts + delivery metadata), not the raw PII export."""
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

    with patch("app.services.gdpr_processor._deliver_customer_export",
               return_value=True) as mock_deliver:
        process_gdpr_request(db, req)

    assert req.status == "completed"
    receipt = json.loads(req.result_summary)
    # Receipt-only schema — counts + delivery metadata, no PII payloads
    assert receipt["phase"] == "delivered"
    assert receipt["delivery_status"] == "sent"
    assert receipt["request_id"] == req.id
    assert receipt["counts"]["orders"] >= 1
    assert receipt["counts"]["events"] >= 3
    assert receipt["counts"]["visitor_ids_found"] >= 1
    assert len(receipt["recipient_hash"]) == 16  # sha256[:16]
    # Delivery actually invoked with the full export blob
    mock_deliver.assert_called_once()


def test_data_request_no_pii_in_result_summary(db):
    """The receipt MUST NOT carry the raw PII export keys
    (orders/events/visitor_state/nudge_events arrays). This is the
    GDPR Art. 5(1)(c) minimisation contract — locked here so any
    regression that re-introduces PII at rest fails CI."""
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

    with patch("app.services.gdpr_processor._deliver_customer_export",
               return_value=True):
        process_gdpr_request(db, req)

    raw = req.result_summary
    receipt = json.loads(raw)
    # Top-level keys must be exactly the receipt schema
    allowed_keys = {
        "phase", "delivery_status", "recipient_hash",
        "request_id", "counts",
    }
    assert set(receipt.keys()).issubset(allowed_keys), (
        f"unexpected keys in receipt: {set(receipt.keys()) - allowed_keys}"
    )
    # The receipt must NOT carry any PII array fields (defense against
    # accidental re-serialization of the export blob into result_summary)
    forbidden_substrings = (
        '"product_url"', '"visitor_id"', '"order_id"',
        '"customer_email"', '"intent_score"', '"dwell"',
    )
    for needle in forbidden_substrings:
        assert needle not in raw, (
            f"PII-shaped key {needle!r} found in result_summary — "
            f"someone re-introduced raw export blob"
        )


def test_data_request_no_customer_data_still_delivers(db):
    """Empty export still delivers (Shopify gets 'no data found'
    instead of silent failure) and persists a zero-count receipt."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="nonexistent",
        customer_email="unknown@example.com",
        status="pending",
    )
    db.add(req)
    db.flush()

    with patch("app.services.gdpr_processor._deliver_customer_export",
               return_value=True):
        process_gdpr_request(db, req)

    assert req.status == "completed"
    receipt = json.loads(req.result_summary)
    assert receipt["counts"]["visitor_ids_found"] == 0
    assert receipt["counts"]["orders"] == 0
    assert receipt["delivery_status"] == "sent"


def test_data_request_no_email_marks_failed(db):
    """Missing customer_email → cannot deliver → status='failed' with
    error_detail; operator must add email + redeliver."""
    db.add(Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active"))
    db.flush()

    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=SHOP_A,
        customer_id="555",
        customer_email=None,  # explicit
        status="pending",
    )
    db.add(req)
    db.flush()

    process_gdpr_request(db, req)

    assert req.status == "failed"
    assert "no customer_email" in (req.error_detail or "")
    # No PII receipt was written (status==failed, processed_at set)
    assert req.processed_at is not None


def test_data_request_delivery_failure_marks_failed(db):
    """Email orchestrator returning False (suppression hit, transient
    network, etc.) → status='failed' + error_detail. The export blob
    is NOT persisted, because we don't keep PII at rest. Operator
    re-triggers via the redeliver endpoint."""
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

    with patch("app.services.gdpr_processor._deliver_customer_export",
               return_value=False):
        process_gdpr_request(db, req)

    assert req.status == "failed"
    assert "could not deliver" in (req.error_detail or "")
    # result_summary stays None when delivery fails — no PII at rest
    assert req.result_summary is None


def test_build_export_receipt_schema():
    """Receipt builder produces the exact allowed-key set + zero-safe
    counts. Locked here so accidental schema drift fails CI."""
    receipt = _build_export_receipt(
        request_id=42,
        counts={
            "visitor_ids_found": 3,
            "orders": [{"order_id": "x"}],
            "events": [{"type": "view"}],
            "visitor_state": None,  # None-safe
            "nudge_events": [],
        },
        delivery_status="sent",
        recipient_hash="0123456789abcdef",
    )
    assert receipt == {
        "phase": "delivered",
        "delivery_status": "sent",
        "recipient_hash": "0123456789abcdef",
        "request_id": 42,
        "counts": {
            "visitor_ids_found": 3,
            "orders": 1,
            "events": 1,
            "visitor_state": 0,
            "nudge_events": 0,
        },
    }
