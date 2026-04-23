"""Tests for Shopify GDPR webhooks — customers/redact, customers/data_request,
shop/redact.

Shopify App Store MANDATES these three endpoints return 2xx on valid HMAC
and 401 on invalid HMAC. A regression breaking them is an app-store-removal
risk: Shopify's compliance team audits apps and removes apps that fail to
ack these webhooks.

Pre-existing coverage at 2026-04-23: none. This file closes that gap by
exercising HMAC verification + GdprRequest row creation for all three
endpoints.
"""
import base64
import hashlib
import hmac
import json
import os

from sqlalchemy import select

from app.models.gdpr_request import GdprRequest


WEBHOOK_SECRET = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "test-webhook-secret")


def _sign_body(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def _customer_redact_body(customer_id: int = 4242001, email: str = "alice@example.com") -> bytes:
    return json.dumps({
        "shop_id": 123,
        "shop_domain": "test-shop-a.myshopify.com",
        "customer": {"id": customer_id, "email": email},
        "orders_to_redact": [],
    }).encode()


def _customer_data_request_body(customer_id: int = 4242002) -> bytes:
    return json.dumps({
        "shop_id": 123,
        "shop_domain": "test-shop-a.myshopify.com",
        "customer": {"id": customer_id, "email": "bob@example.com"},
        "orders_requested": [],
    }).encode()


def _shop_redact_body() -> bytes:
    return json.dumps({
        "shop_id": 123,
        "shop_domain": "test-shop-a.myshopify.com",
    }).encode()


# ---------------------------------------------------------------------------
# customers/redact
# ---------------------------------------------------------------------------

def test_customers_redact_valid_hmac_queues_gdpr_request(client, merchant_a, db):
    from tests.conftest import SHOP_A
    body = _customer_redact_body(customer_id=4242101)
    resp = client.post(
        "/webhooks/shopify/customers-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": _sign_body(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    gdpr_id = data["gdpr_request_id"]

    row = db.get(GdprRequest, gdpr_id)
    assert row is not None
    assert row.request_type == "customers_redact"
    assert row.shop_domain == SHOP_A
    assert row.customer_id == "4242101"


def test_customers_redact_invalid_hmac_rejected(client, merchant_a):
    from tests.conftest import SHOP_A
    body = _customer_redact_body(customer_id=4242102)
    resp = client.post(
        "/webhooks/shopify/customers-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": "dGhpcyBpcyBub3QgYSB2YWxpZCBobWFj",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_customers_redact_missing_hmac_rejected(client, merchant_a):
    from tests.conftest import SHOP_A
    body = _customer_redact_body(customer_id=4242103)
    resp = client.post(
        "/webhooks/shopify/customers-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# customers/data_request
# ---------------------------------------------------------------------------

def test_customers_data_request_valid_hmac_queues(client, merchant_a, db):
    from tests.conftest import SHOP_A
    body = _customer_data_request_body(customer_id=4242201)
    resp = client.post(
        "/webhooks/shopify/customers-data-request",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": _sign_body(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    row = db.get(GdprRequest, data["gdpr_request_id"])
    assert row is not None
    assert row.request_type == "customers_data_request"
    assert row.customer_id == "4242201"


def test_customers_data_request_invalid_hmac_rejected(client, merchant_a):
    from tests.conftest import SHOP_A
    body = _customer_data_request_body()
    resp = client.post(
        "/webhooks/shopify/customers-data-request",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": "dGhpcyBpcyBub3QgYSB2YWxpZCBobWFj",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# shop/redact
# ---------------------------------------------------------------------------

def test_shop_redact_valid_hmac_queues_and_marks_uninstalled(client, merchant_a, db):
    from tests.conftest import SHOP_A
    # merchant_a starts with install_status='active'
    assert merchant_a.install_status == "active"

    body = _shop_redact_body()
    resp = client.post(
        "/webhooks/shopify/shop-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": _sign_body(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"

    gdpr_row = db.get(GdprRequest, data["gdpr_request_id"])
    assert gdpr_row is not None
    assert gdpr_row.request_type == "shop_redact"
    assert gdpr_row.shop_domain == SHOP_A

    # shop-redact also defensively marks merchant uninstalled if it was
    # still 'active' — simulates Shopify's 48h post-uninstall delivery
    # when app-uninstalled webhook was missed.
    db.refresh(merchant_a)
    assert merchant_a.install_status == "uninstalled"
    assert merchant_a.access_token is None
    assert merchant_a.billing_active is False


def test_shop_redact_invalid_hmac_rejected(client, merchant_a):
    from tests.conftest import SHOP_A
    body = _shop_redact_body()
    resp = client.post(
        "/webhooks/shopify/shop-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "X-Shopify-Hmac-Sha256": "dGhpcyBpcyBub3QgYSB2YWxpZCBobWFj",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_shop_redact_missing_hmac_rejected(client, merchant_a):
    from tests.conftest import SHOP_A
    body = _shop_redact_body()
    resp = client.post(
        "/webhooks/shopify/shop-redact",
        content=body,
        headers={
            "X-Shopify-Shop-Domain": SHOP_A,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
