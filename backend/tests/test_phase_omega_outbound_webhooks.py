"""
Phase Ω ecosystem #1 — outbound webhook system tests.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from app.services.outbound_webhooks import (
    create_subscription,
    revoke_subscription,
    publish_event,
    attempt_delivery,
    sign_payload,
    build_signature_header,
    deliver_pending_batch,
    generate_secret,
)
from app.models.outbound_webhook import OutboundWebhookDelivery, OutboundWebhookSubscription


SHOP = "owh-test.myshopify.com"


def test_generate_secret_length():
    s = generate_secret()
    assert len(s) == 64
    assert all(c in "0123456789abcdef" for c in s)


def test_sign_payload_deterministic():
    sig = sign_payload("secret123", b'{"hello":"world"}', "2026-04-13T10:00:00")
    assert len(sig) == 64
    # Same inputs → same output
    sig2 = sign_payload("secret123", b'{"hello":"world"}', "2026-04-13T10:00:00")
    assert sig == sig2


def test_sign_payload_different_secrets_differ():
    sig1 = sign_payload("a", b"body", "ts")
    sig2 = sign_payload("b", b"body", "ts")
    assert sig1 != sig2


def test_build_signature_header_format():
    ts, header = build_signature_header("secret", b"{}")
    assert header.startswith("t=")
    assert ",sig=" in header
    assert ts in header


def test_create_subscription(db):
    sub = create_subscription(db, SHOP, "https://example.com/hook", ["nudge.fired"], description="test")
    assert sub.id is not None
    assert sub.status == "active"
    assert sub.secret
    assert sub.event_types == ["nudge.fired"]


def test_revoke_subscription(db):
    sub = create_subscription(db, SHOP, "https://example.com/hook", ["*"])
    ok = revoke_subscription(db, SHOP, sub.id)
    assert ok is True
    db.refresh(sub)
    assert sub.status == "disabled"


def test_revoke_subscription_wrong_shop(db):
    sub = create_subscription(db, SHOP, "https://example.com/hook", ["*"])
    ok = revoke_subscription(db, "other.myshopify.com", sub.id)
    assert ok is False


def test_publish_event_no_matching_subscription(db):
    # No subs at all
    ids = publish_event(db, SHOP, "nudge.fired", {"x": 1})
    assert ids == []


def test_publish_event_matches_wildcard(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock(status_code=200, text="ok")
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        ids = publish_event(db, SHOP, "nudge.fired", {"id": 99})
    assert len(ids) == 1
    d = db.get(OutboundWebhookDelivery, ids[0])
    assert d.status == "delivered"
    assert d.attempts == 1


def test_publish_event_skips_non_matching_event(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["rars.spike"])
    ids = publish_event(db, SHOP, "nudge.fired", {})
    assert ids == []


def test_attempt_delivery_failure_marks_pending(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock(status_code=500, text="boom")
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        ids = publish_event(db, SHOP, "nudge.fired", {})
    d = db.get(OutboundWebhookDelivery, ids[0])
    assert d.status == "pending"
    assert d.attempts == 1
    assert d.response_status == 500
    db.refresh(sub)
    assert sub.consecutive_failures == 1


def test_attempt_delivery_max_attempts_dead(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock(status_code=500, text="boom")
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        publish_event(db, SHOP, "nudge.fired", {})
        d = db.query(OutboundWebhookDelivery).filter_by(shop_domain=SHOP).first()
        # Hammer it past max attempts
        for _ in range(6):
            attempt_delivery(db, d.id)
        db.refresh(d)
    assert d.status == "dead"
    assert d.attempts >= 6


def test_transport_error_recorded(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    with patch("httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.side_effect = RuntimeError("dns fail")
        ids = publish_event(db, SHOP, "nudge.fired", {})
    d = db.get(OutboundWebhookDelivery, ids[0])
    assert "transport_error" in d.response_body
    assert d.response_status == 0


def test_auto_disable_after_threshold(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    sub.consecutive_failures = 19
    db.flush()
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock(status_code=500, text="x")
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        publish_event(db, SHOP, "nudge.fired", {})
    db.refresh(sub)
    assert sub.auto_disabled is True
    assert sub.status == "disabled"


def test_deliver_pending_batch_skips_recent(db):
    sub = create_subscription(db, SHOP, "https://example.com/h", ["*"])
    with patch("httpx.Client") as mock_client:
        mock_resp = MagicMock(status_code=500, text="x")
        mock_client.return_value.__enter__.return_value.post.return_value = mock_resp
        publish_event(db, SHOP, "nudge.fired", {})
    # last_attempted_at is now → next attempt should be skipped (backoff)
    result = deliver_pending_batch(db, limit=10)
    assert result["skipped"] >= 1


# ---------------------------------------------------------------------------
# API smoke tests
# ---------------------------------------------------------------------------


def test_api_create_and_list(client, auth_a):
    r = client.post(
        "/pro/webhooks/subscriptions",
        json={
            "target_url": "https://example.com/hook",
            "event_types": ["nudge.fired", "rars.spike"],
            "description": "my zap",
        },
        cookies=auth_a,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] > 0
    assert "secret_revealed_once" in body
    assert body["event_types"] == ["nudge.fired", "rars.spike"]

    r2 = client.get("/pro/webhooks/subscriptions", cookies=auth_a)
    assert r2.status_code == 200
    subs = r2.json()["subscriptions"]
    assert len(subs) >= 1


def test_api_rejects_unknown_event(client, auth_a):
    r = client.post(
        "/pro/webhooks/subscriptions",
        json={"target_url": "https://example.com/h", "event_types": ["bogus.event"]},
        cookies=auth_a,
    )
    assert r.status_code == 400


def test_api_pause_and_delete(client, auth_a):
    r = client.post(
        "/pro/webhooks/subscriptions",
        json={"target_url": "https://example.com/hook", "event_types": ["*"]},
        cookies=auth_a,
    )
    sid = r.json()["id"]
    r2 = client.patch(
        f"/pro/webhooks/subscriptions/{sid}",
        json={"status": "paused"},
        cookies=auth_a,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "paused"
    r3 = client.delete(
        f"/pro/webhooks/subscriptions/{sid}",
        cookies=auth_a,
        headers={"Content-Type": "application/json"},
    )
    assert r3.status_code == 200
