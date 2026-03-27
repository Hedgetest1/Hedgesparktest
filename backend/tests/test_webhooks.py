"""Tests for Shopify webhook HMAC verification and order ingestion."""
import base64
import hashlib
import hmac
import json
import os


WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "test-webhook-secret")


def _sign_body(body: bytes) -> str:
    """Compute valid Shopify HMAC for test body."""
    return base64.b64encode(
        hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_hmac_valid_signature(client, merchant_a):
    """Valid HMAC → order ingested (200)."""
    from tests.conftest import SHOP_A
    body = json.dumps({
        "id": 9999001,
        "total_price": "49.99",
        "currency": "USD",
        "line_items": [],
    }).encode()
    headers = {
        "X-Shopify-Shop-Domain": SHOP_A,
        "X-Shopify-Hmac-Sha256": _sign_body(body),
        "Content-Type": "application/json",
    }
    resp = client.post("/webhooks/shopify/orders", content=body, headers=headers)
    assert resp.status_code == 200


def test_hmac_invalid_signature_rejected(client, merchant_a):
    """Wrong HMAC → 401."""
    from tests.conftest import SHOP_A
    body = json.dumps({"id": 9999002, "total_price": "10.00", "currency": "USD"}).encode()
    headers = {
        "X-Shopify-Shop-Domain": SHOP_A,
        "X-Shopify-Hmac-Sha256": "dGhpcyBpcyBub3QgYSB2YWxpZCBobWFj",
        "Content-Type": "application/json",
    }
    resp = client.post("/webhooks/shopify/orders", content=body, headers=headers)
    assert resp.status_code == 401


def test_hmac_missing_header_rejected(client, merchant_a):
    """No HMAC header at all → 401."""
    from tests.conftest import SHOP_A
    body = json.dumps({"id": 9999003, "total_price": "10.00"}).encode()
    headers = {
        "X-Shopify-Shop-Domain": SHOP_A,
        "Content-Type": "application/json",
    }
    resp = client.post("/webhooks/shopify/orders", content=body, headers=headers)
    assert resp.status_code == 401
