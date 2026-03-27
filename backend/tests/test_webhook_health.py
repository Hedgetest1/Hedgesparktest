"""Tests for webhook health detection (webhook_health.py)."""
import os
from unittest.mock import patch, MagicMock

from sqlalchemy.orm import Session

from app.core.token_crypto import encrypt_token
from app.models.merchant import Merchant
from app.services.webhook_health import (
    check_webhook_health,
    EXPECTED_WEBHOOKS,
    WebhookHealthReport,
)
from tests.conftest import SHOP_A


def _setup_merchant_with_token(db: Session) -> Merchant:
    m = db.query(Merchant).filter(Merchant.shop_domain == SHOP_A).first()
    if not m:
        m = Merchant(shop_domain=SHOP_A, plan="pro", billing_active=True, install_status="active")
        db.add(m)
        db.flush()
    m.access_token = encrypt_token("shpat_test_token_123")
    db.flush()
    return m


def test_healthy_when_webhook_registered(db, merchant_a):
    """When Shopify returns the expected webhook, report is healthy."""
    _setup_merchant_with_token(db)
    app_url = os.getenv("APP_URL", "https://api.hedgesparkhq.com")

    mock_webhooks = [
        {"id": 12345, "topic": "app/uninstalled", "address": f"{app_url}/webhooks/shopify/app-uninstalled"}
    ]

    with patch("app.services.webhook_health._list_all_webhooks", return_value=mock_webhooks), \
         patch("app.services.webhook_health._APP_URL", app_url):
        report = check_webhook_health(db, SHOP_A)

    assert report.healthy is True
    assert len(report.ok) == 1
    assert len(report.missing) == 0


def test_missing_webhook_detected(db, merchant_a):
    """When Shopify returns no webhooks, report shows missing."""
    _setup_merchant_with_token(db)
    app_url = os.getenv("APP_URL", "https://api.hedgesparkhq.com")

    with patch("app.services.webhook_health._list_all_webhooks", return_value=[]), \
         patch("app.services.webhook_health._APP_URL", app_url):
        report = check_webhook_health(db, SHOP_A)

    assert report.healthy is False
    assert "app/uninstalled" in report.missing


def test_stale_webhook_detected(db, merchant_a):
    """When webhook exists but with wrong URL, report shows stale."""
    _setup_merchant_with_token(db)
    app_url = os.getenv("APP_URL", "https://api.hedgesparkhq.com")

    mock_webhooks = [
        {"id": 99999, "topic": "app/uninstalled", "address": "https://old-url.example.com/webhook"}
    ]

    with patch("app.services.webhook_health._list_all_webhooks", return_value=mock_webhooks), \
         patch("app.services.webhook_health._APP_URL", app_url):
        report = check_webhook_health(db, SHOP_A)

    assert report.healthy is False
    assert "app/uninstalled" in report.stale
    assert report.details[0].stale is True


def test_no_token_returns_error(db, merchant_a):
    """Merchant without token → error report, not crash."""
    # merchant_a has no access_token by default
    report = check_webhook_health(db, SHOP_A)
    assert report.healthy is False
    assert report.error is not None
