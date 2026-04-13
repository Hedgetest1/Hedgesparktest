"""Tests for webhook health scheduling and worker logging normalization."""
import time
from unittest.mock import patch, MagicMock

from app.services.webhook_health import (
    check_webhook_health,
    WebhookHealthReport,
)


# ---------------------------------------------------------------------------
# 24h scheduling guard
# ---------------------------------------------------------------------------

def test_should_run_webhook_check_first_time():
    """First run ever (None) → should run."""
    from app.workers.tasks import webhook_health_task as wht
    original = wht._last_run
    try:
        wht._last_run = None
        assert wht.should_run() is True
    finally:
        wht._last_run = original


def test_should_not_run_within_24h():
    """Run 1 second ago → should NOT run."""
    from app.workers.tasks import webhook_health_task as wht
    original = wht._last_run
    try:
        wht._last_run = time.monotonic() - 1
        assert wht.should_run() is False
    finally:
        wht._last_run = original


def test_should_run_after_24h():
    """Run 25 hours ago → should run."""
    from app.workers.tasks import webhook_health_task as wht
    original = wht._last_run
    try:
        wht._last_run = time.monotonic() - (25 * 3600)
        assert wht.should_run() is True
    finally:
        wht._last_run = original


# ---------------------------------------------------------------------------
# Skip behavior
# ---------------------------------------------------------------------------

def test_skip_merchant_without_token(db, merchant_a):
    """Merchant with no access_token → error report, no crash."""
    from tests.conftest import SHOP_A
    report = check_webhook_health(db, SHOP_A)
    assert report.healthy is False
    assert "token" in (report.error or "").lower()


def test_skip_nonexistent_merchant(db):
    """Non-existent shop → error report, no crash."""
    report = check_webhook_health(db, "nonexistent.myshopify.com")
    assert report.healthy is False
    assert report.error is not None


# ---------------------------------------------------------------------------
# Worker logging context
# ---------------------------------------------------------------------------

def test_worker_logging_produces_json():
    """Worker log() calls produce JSON via the structured formatter."""
    import json
    import io
    import logging
    from app.core.logging_config import JSONFormatter, set_worker_context

    set_worker_context(worker_name="test_worker")
    try:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="worker.test", level=logging.INFO, pathname="", lineno=0,
            msg="test cycle complete", args=None, exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["worker"] == "test_worker"
        assert data["message"] == "test cycle complete"
        assert data["level"] == "INFO"
    finally:
        from app.core.logging_config import clear_request_context
        clear_request_context()
