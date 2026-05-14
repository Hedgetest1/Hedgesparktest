"""GDPR Art. 15 auto-delivery — customers_data_request now emails the
export artifact to the data subject via the governed email orchestrator."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from app.services.gdpr_processor import (
    _deliver_customer_export,
    _hash_email,
    _process_customers_data_request,
)
from app.models.gdpr_request import GdprRequest


def test_hash_email_is_stable():
    a = _hash_email("alice@example.com")
    b = _hash_email("alice@example.com")
    c = _hash_email("bob@example.com")
    assert a == b
    assert a != c
    assert len(a) == 16


def test_hash_email_empty_safe():
    assert _hash_email("") == _hash_email("")
    assert _hash_email(None) is not None  # type: ignore[arg-type]


def test_deliver_routes_through_orchestrator(db):
    calls: dict = {}

    def _fake_send_immediate(db_, intent):
        calls["intent"] = intent
        return {"status": "sent", "reason": None, "resend_id": "x"}

    with patch(
        "app.services.email_orchestrator.send_immediate",
        side_effect=_fake_send_immediate,
    ):
        result = _deliver_customer_export(
            db=db,
            customer_email="alice@example.com",
            shop_domain="test.myshopify.com",
            export_json='{"foo":"bar"}',
            request_id=42,
        )
    assert result is True
    intent = calls["intent"]
    assert intent.to_email == "alice@example.com"
    assert intent.email_type == "gdpr_export"
    assert intent.producer == "gdpr_processor"
    assert "request #42" in intent.subject
    assert "foo" in intent.html  # export inlined


def test_deliver_returns_false_when_orchestrator_blocks(db):
    def _fake_blocked(db_, intent):
        return {"status": "blocked", "reason": "suppressed", "resend_id": None}

    with patch(
        "app.services.email_orchestrator.send_immediate",
        side_effect=_fake_blocked,
    ):
        result = _deliver_customer_export(
            db=db,
            customer_email="bob@example.com",
            shop_domain="t.myshopify.com",
            export_json='{"k":1}',
            request_id=7,
        )
    assert result is False


def test_deliver_returns_false_on_exception(db):
    with patch(
        "app.services.email_orchestrator.send_immediate",
        side_effect=Exception("mail provider down"),
    ):
        result = _deliver_customer_export(
            db=db,
            customer_email="x@y.io",
            shop_domain="z.myshopify.com",
            export_json="{}",
            request_id=1,
        )
    assert result is False


def test_processor_returns_delivery_receipt_only(db):
    """Full path: _process_customers_data_request returns a RECEIPT
    (counts + delivery metadata) — never the raw PII export. Receipt
    schema locked in `_build_export_receipt`; this is the integration
    contract."""
    req = GdprRequest(
        request_type="customers_data_request",
        shop_domain="delivery-test.myshopify.com",
        customer_id=None,
        customer_email=f"alice_{uuid.uuid4().hex[:6]}@example.com",
        status="pending",
    )
    db.add(req)
    db.flush()

    with patch(
        "app.services.email_orchestrator.send_immediate",
        side_effect=lambda db_, intent: {"status": "sent", "reason": None, "resend_id": "x"},
    ):
        summary = _process_customers_data_request(db, req)

    receipt = json.loads(summary)
    assert receipt["phase"] == "delivered"
    assert receipt["delivery_status"] == "sent"
    assert receipt["recipient_hash"] == _hash_email(req.customer_email)
    assert receipt["request_id"] == req.id
    # No raw PII fields in the receipt
    assert "data" not in receipt
    assert "customer_email" not in receipt
    assert "shop_domain" not in receipt
    # Counts present and zero-shaped (empty shop, no source data)
    assert receipt["counts"] == {
        "visitor_ids_found": 0,
        "orders": 0,
        "events": 0,
        "visitor_state": 0,
        "nudge_events": 0,
    }
