"""Tests for email-deliverability → self-healing pipeline wiring.

Resend bounce/complaint events and email_orchestrator send failures
must write ops_alerts so the generic Rule 7 catch-all can triage them.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_email_orchestrator_send_failed_writes_alert():
    """When Resend rejects the request, _send_intent must emit an alert."""
    from app.services import email_orchestrator as eo

    intent = MagicMock()
    intent.intent_id = "intent_xyz"
    intent.shop_domain = "alert-shop.myshopify.com"
    intent.email_type = "merchant_digest"
    intent.to_email = "owner@alert-shop.com"
    intent.html = "<p>hi</p>"
    intent.plain_text = "hi"
    intent.subject = "x"
    intent.from_address = "no-reply@hedgesparkhq.com"
    intent.producer = "merchant_digest_worker"

    fake_db = MagicMock()
    gov = MagicMock(passed=True, content_hash="abc")

    with patch("app.services.email_governance.validate_intent", return_value=gov), \
         patch("app.core.resend_usage.get_resend_usage", return_value={"sent": 0}), \
         patch.object(eo, "_claim_send_slot", return_value=True), \
         patch("app.core.email.send_email", return_value=""), \
         patch("app.services.alerting.write_alert") as mock_alert:
        ok = eo._send_intent(fake_db, intent)

    assert ok is False
    assert mock_alert.called
    kwargs = mock_alert.call_args.kwargs
    assert kwargs["alert_type"] == "email_send_failed"
    assert kwargs["shop_domain"] == "alert-shop.myshopify.com"
    assert kwargs["severity"] == "warning"
    assert "merchant_digest" in kwargs["source"]


def test_email_orchestrator_send_success_no_alert():
    """A successful send must NOT write an alert."""
    from app.services import email_orchestrator as eo

    intent = MagicMock()
    intent.intent_id = "intent_ok"
    intent.shop_domain = "happy.myshopify.com"
    intent.email_type = "welcome"
    intent.to_email = "owner@happy.com"
    intent.html = "<p>hi</p>"
    intent.plain_text = "hi"
    intent.subject = "x"
    intent.from_address = "no-reply@hedgesparkhq.com"
    intent.producer = "p"
    intent.priority = MagicMock(name="HIGH")

    fake_db = MagicMock()
    gov = MagicMock(passed=True, content_hash="abc")

    with patch("app.services.email_governance.validate_intent", return_value=gov), \
         patch("app.core.resend_usage.get_resend_usage", return_value={"sent": 0}), \
         patch.object(eo, "_claim_send_slot", return_value=True), \
         patch("app.core.email.send_email", return_value="re_123"), \
         patch.object(eo, "_log_sent"), \
         patch.object(eo, "_increment_send_counter"), \
         patch("app.services.email_performance.record_email_event"), \
         patch("app.services.alerting.write_alert") as mock_alert:
        ok = eo._send_intent(fake_db, intent)

    assert ok is True
    assert not mock_alert.called


def test_resend_bounce_emits_alert(monkeypatch):
    """A bounce event must write an ops_alert with severity=warning."""
    import json as _json
    from fastapi.testclient import TestClient

    from app.main import app

    # Disable Resend signature verification for the test
    monkeypatch.setattr(
        "app.api.resend_webhooks._verify_webhook",
        lambda payload, headers: None,
    )

    captured: list[dict] = []

    def _fake_write_alert(db, **kwargs):
        captured.append(kwargs)
        return MagicMock(id=1)

    monkeypatch.setattr("app.services.alerting.write_alert", _fake_write_alert)

    # Stub the merchant_emails lookup so we resolve a shop_domain
    from app.api import resend_webhooks as rw_mod

    class _FakeMerchantEmail:
        shop_domain = "bounce-shop.myshopify.com"
        email_type = "merchant_digest"

    class _FakeQuery:
        def filter(self, *a, **k): return self
        def first(self):
            return _FakeMerchantEmail()

    class _FakeJourneyQuery:
        def filter(self, *a, **k): return self
        def first(self): return None

    real_get_db = rw_mod.get_db

    def _fake_get_db():
        db = MagicMock()

        def query(model):
            name = getattr(model, "__name__", str(model))
            if "MerchantEmail" in name:
                return _FakeQuery()
            return _FakeJourneyQuery()

        db.query.side_effect = query
        db.commit = MagicMock()
        db.add = MagicMock()
        yield db

    app.dependency_overrides[real_get_db] = _fake_get_db
    try:
        client = TestClient(app)
        body = {
            "type": "email.bounced",
            "created_at": "2026-04-11T12:00:00Z",
            "data": {
                "email_id": "re_test_bounce",
                "to": ["bounced@example.com"],
            },
        }
        r = client.post("/webhooks/resend/events", content=_json.dumps(body))
    finally:
        app.dependency_overrides.pop(real_get_db, None)

    assert r.status_code == 200
    assert any(c.get("alert_type") == "email_bounced" for c in captured), captured
    bounce_alert = next(c for c in captured if c.get("alert_type") == "email_bounced")
    assert bounce_alert["severity"] == "warning"
    assert bounce_alert["shop_domain"] == "bounce-shop.myshopify.com"


def test_resend_complaint_emits_critical_alert(monkeypatch):
    """A spam complaint is more serious than a bounce → severity=critical."""
    import json as _json
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(
        "app.api.resend_webhooks._verify_webhook",
        lambda payload, headers: None,
    )

    captured: list[dict] = []
    monkeypatch.setattr(
        "app.services.alerting.write_alert",
        lambda db, **kwargs: captured.append(kwargs) or MagicMock(id=1),
    )

    from app.api import resend_webhooks as rw_mod

    class _FakeMerchantEmail:
        shop_domain = "complaint-shop.myshopify.com"
        email_type = "weekly_digest"

    class _FakeQuery:
        def filter(self, *a, **k): return self
        def first(self): return _FakeMerchantEmail()

    class _FakeJourneyQuery:
        def filter(self, *a, **k): return self
        def first(self): return None

    real_get_db = rw_mod.get_db

    def _fake_get_db():
        db = MagicMock()

        def query(model):
            name = getattr(model, "__name__", str(model))
            if "MerchantEmail" in name:
                return _FakeQuery()
            return _FakeJourneyQuery()

        db.query.side_effect = query
        db.commit = MagicMock()
        db.add = MagicMock()
        yield db

    app.dependency_overrides[real_get_db] = _fake_get_db
    try:
        client = TestClient(app)
        body = {
            "type": "email.complained",
            "created_at": "2026-04-11T12:00:00Z",
            "data": {
                "email_id": "re_test_complaint",
                "to": ["angry@example.com"],
            },
        }
        r = client.post("/webhooks/resend/events", content=_json.dumps(body))
    finally:
        app.dependency_overrides.pop(real_get_db, None)

    assert r.status_code == 200
    complaint = next((c for c in captured if c.get("alert_type") == "email_complained"), None)
    assert complaint is not None
    assert complaint["severity"] == "critical"
