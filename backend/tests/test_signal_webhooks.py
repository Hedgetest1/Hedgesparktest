"""Tests for F8 — outbound signal webhooks."""
from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch, MagicMock

import pytest

from app.services.signal_webhooks import (
    SIGNAL_EVENTS,
    _build_slack_payload,
    _is_slack_url,
    _sign_payload,
    create_webhook,
    delete_webhook,
    emit_signal,
    get_or_create_secret,
    list_webhooks,
    verify_signature,
)


def test_sign_and_verify():
    secret = "test-secret"
    body = b'{"event":"test"}'
    sig = _sign_payload(secret, body)
    assert len(sig) == 64  # sha256 hex
    assert verify_signature(secret, body, sig) is True
    assert verify_signature(secret, body, "wrong-signature") is False


def test_verify_is_constant_time():
    """hmac.compare_digest ensures no timing attack — we just check it doesn't crash."""
    secret = "test"
    body = b"x"
    sig = _sign_payload(secret, body)
    assert verify_signature(secret, body, sig) is True
    # Different length
    assert verify_signature(secret, body, "short") is False


def test_create_webhook_validates_url():
    with pytest.raises(ValueError):
        create_webhook("shop.myshopify.com", url="http://insecure.com", events=["test_ping"])


def test_create_webhook_validates_events():
    with pytest.raises(ValueError):
        create_webhook("shop.myshopify.com", url="https://ok.com", events=["bogus_event"])
    with pytest.raises(ValueError):
        create_webhook("shop.myshopify.com", url="https://ok.com", events=[])


def test_create_and_list_webhook():
    shop = "webhook-crud.myshopify.com"
    wh = create_webhook(shop, url="https://example.com/hs", events=["high_intent_abandon"])
    if wh is None:
        pytest.skip("redis unavailable")

    listed = list_webhooks(shop)
    assert any(w.id == wh.id for w in listed)
    assert wh.url == "https://example.com/hs"
    assert "high_intent_abandon" in wh.events


def test_delete_webhook():
    shop = "webhook-delete.myshopify.com"
    wh = create_webhook(shop, url="https://x.example.com/y", events=["goal_at_risk"])
    if wh is None:
        pytest.skip("redis unavailable")
    assert delete_webhook(shop, wh.id) is True
    assert delete_webhook(shop, wh.id) is False


def test_get_or_create_secret_is_stable():
    """Second call for the same shop returns the same secret."""
    shop = "webhook-secret.myshopify.com"
    s1 = get_or_create_secret(shop)
    if not s1:
        pytest.skip("redis unavailable")
    s2 = get_or_create_secret(shop)
    assert s1 == s2
    assert len(s1) >= 32


def test_emit_signal_to_no_webhooks_is_noop():
    """A shop with no configured webhooks emits cleanly."""
    results = emit_signal(
        "no-webhooks-shop.myshopify.com",
        event_type="test_ping",
        payload={"hello": "world"},
    )
    assert results == []


def test_emit_signal_unknown_event_ignored():
    results = emit_signal(
        "any-shop.myshopify.com",
        event_type="not_a_real_event",
        payload={},
    )
    assert results == []


def test_emit_signal_delivers_with_signature():
    """End-to-end: configure webhook, emit signal, verify httpx.post was called
    with a valid signature."""
    # Use a unique shop each run so Redis leftovers from other tests don't
    # inflate the result count
    import uuid
    shop = f"webhook-deliver-{uuid.uuid4().hex[:8]}.myshopify.com"
    wh = create_webhook(
        shop, url="https://catch.example.com/hook", events=["test_ping"],
    )
    if wh is None:
        pytest.skip("redis unavailable")

    mock_response = MagicMock(status_code=200)
    with patch("httpx.post", return_value=mock_response) as mock_post:
        results = emit_signal(
            shop, event_type="test_ping", payload={"t": 1},
        )
    assert len(results) == 1
    assert results[0].status == "delivered"
    assert mock_post.called
    # Verify the headers include signature + event id
    call_kwargs = mock_post.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "X-HedgeSpark-Signature" in headers
    assert "X-HedgeSpark-Event-ID" in headers
    assert headers["X-HedgeSpark-Event-Type"] == "test_ping"


def test_is_slack_url_detection():
    assert _is_slack_url("https://hooks.slack.com/services/T00/B00/xxx") is True
    assert _is_slack_url("https://HOOKS.SLACK.COM/services/T00/B00/xxx") is True
    assert _is_slack_url("https://example.com/slack") is False
    assert _is_slack_url("not-a-url") is False


def test_build_slack_payload_shape():
    payload = _build_slack_payload(
        "goal_at_risk",
        "shop.myshopify.com",
        source="goals",
        payload={"goal": "revenue", "target": 50000, "current": 42000},
    )
    assert "text" in payload
    assert "slipping" in payload["text"].lower()
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    assert blocks[1]["type"] == "context"
    section = next(b for b in blocks if b["type"] == "section")
    field_texts = " ".join(f["text"] for f in section["fields"])
    assert "Goal" in field_texts
    assert "42000" in field_texts


def test_emit_signal_slack_url_uses_block_kit():
    """Slack webhook URLs get Block Kit payload, no HMAC headers."""
    import uuid
    shop = f"slack-deliver-{uuid.uuid4().hex[:8]}.myshopify.com"
    wh = create_webhook(
        shop,
        url="https://hooks.slack.com/services/T00000/B00000/secret",
        events=["test_ping"],
    )
    if wh is None:
        pytest.skip("redis unavailable")

    mock_response = MagicMock(status_code=200)
    with patch("httpx.post", return_value=mock_response) as mock_post:
        results = emit_signal(shop, event_type="test_ping", payload={"hi": "there"})

    assert len(results) == 1
    assert results[0].status == "delivered"

    call_kwargs = mock_post.call_args.kwargs
    headers = call_kwargs.get("headers", {})
    assert "X-HedgeSpark-Signature" not in headers
    assert "X-HedgeSpark-Event-ID" not in headers

    import json as _json
    body = _json.loads(call_kwargs.get("content"))
    assert "blocks" in body
    assert any(b.get("type") == "header" for b in body["blocks"])


def test_emit_signal_non_slack_still_signs():
    """Non-Slack URLs keep the raw JSON + HMAC signature path."""
    import uuid
    shop = f"json-deliver-{uuid.uuid4().hex[:8]}.myshopify.com"
    wh = create_webhook(
        shop, url="https://example.com/webhook", events=["test_ping"],
    )
    if wh is None:
        pytest.skip("redis unavailable")

    mock_response = MagicMock(status_code=200)
    with patch("httpx.post", return_value=mock_response) as mock_post:
        emit_signal(shop, event_type="test_ping", payload={"x": 1})

    headers = mock_post.call_args.kwargs.get("headers", {})
    assert "X-HedgeSpark-Signature" in headers


def test_emit_signal_records_failure():
    """A failing webhook gets status=failed in the result."""
    shop = "webhook-fail.myshopify.com"
    wh = create_webhook(
        shop, url="https://catch.example.com/hook", events=["goal_at_risk"],
    )
    if wh is None:
        pytest.skip("redis unavailable")

    mock_response = MagicMock(status_code=500)
    with patch("httpx.post", return_value=mock_response):
        results = emit_signal(shop, event_type="goal_at_risk", payload={})
    assert len(results) >= 1
    assert results[0].status == "failed"
