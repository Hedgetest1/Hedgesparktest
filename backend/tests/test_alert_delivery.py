"""Tests for alert persistence + external delivery behavior."""
import os
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.services.alerting import write_alert
from app.core.alert_delivery import deliver_alert_externally, _EXTERNAL_ALERT_TYPES


# ---------------------------------------------------------------------------
# DB persistence always works regardless of delivery
# ---------------------------------------------------------------------------

def test_alert_persists_even_if_delivery_fails(db):
    """Alert row exists in DB even when external delivery raises."""
    with patch("app.core.alert_delivery.deliver_alert_externally", side_effect=Exception("boom")):
        alert = write_alert(
            db, severity="critical", source="test",
            alert_type="gdpr_failure", summary="test persistence",
        )
    assert alert.id is not None
    row = db.execute(text("SELECT id FROM ops_alerts WHERE id = :id"), {"id": alert.id}).fetchone()
    assert row is not None


def test_alert_persists_without_slack_configured(db):
    """Alert works normally when OPS_SLACK_WEBHOOK_URL is not set."""
    alert = write_alert(
        db, severity="warning", source="test",
        alert_type="webhook_repair_failed", summary="no slack url",
    )
    assert alert.id is not None
    assert alert.severity == "warning"


# ---------------------------------------------------------------------------
# External delivery routing
# ---------------------------------------------------------------------------

def test_delivery_noop_when_no_url():
    """No OPS_SLACK_WEBHOOK_URL → returns False, no HTTP call."""
    with patch("app.core.alert_delivery._SLACK_URL", ""):
        result = deliver_alert_externally(
            severity="critical", source="test",
            alert_type="gdpr_failure", summary="test",
        )
    assert result is False


def test_delivery_sends_when_url_configured():
    """With URL configured + eligible alert → POST to Slack."""
    mock_resp = MagicMock(status_code=200)
    with patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", return_value=mock_resp) as mock_post:
        result = deliver_alert_externally(
            severity="critical", source="gdpr_processor",
            alert_type="gdpr_failure", summary="GDPR failed",
            shop_domain="test.myshopify.com",
        )
    assert result is True
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "hooks.slack.com" in call_kwargs[0][0]
    payload = call_kwargs[1]["json"]
    assert "CRITICAL" in payload["text"]
    assert "gdpr_failure" in payload["text"]


def test_delivery_skips_low_severity_non_critical():
    """Info alert for non-critical type → not delivered externally."""
    with patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"):
        result = deliver_alert_externally(
            severity="info", source="test",
            alert_type="webhook_repaired", summary="minor",
        )
    assert result is False


def test_delivery_sends_critical_regardless_of_type():
    """Critical severity → always delivered, even if type is not in the set."""
    mock_resp = MagicMock(status_code=200)
    with patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", return_value=mock_resp):
        result = deliver_alert_externally(
            severity="critical", source="test",
            alert_type="unknown_but_critical", summary="critical alert",
        )
    assert result is True


def test_delivery_handles_http_failure_gracefully():
    """Slack returns 500 → returns False, no exception."""
    mock_resp = MagicMock(status_code=500)
    with patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", return_value=mock_resp):
        result = deliver_alert_externally(
            severity="critical", source="test",
            alert_type="gdpr_failure", summary="test",
        )
    assert result is False


def test_delivery_handles_network_error_gracefully():
    """httpx raises → returns False, no exception."""
    with patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", side_effect=Exception("network down")):
        result = deliver_alert_externally(
            severity="critical", source="test",
            alert_type="gdpr_failure", summary="test",
        )
    assert result is False


# ---------------------------------------------------------------------------
# Worker watchdog
# ---------------------------------------------------------------------------

def test_watchdog_scheduling_guard():
    """Watchdog guard respects interval."""
    import time
    import app.workers.aggregation_worker as aw
    original = aw._last_watchdog_run
    try:
        aw._last_watchdog_run = None
        assert aw._should_run_watchdog() is True
        aw._last_watchdog_run = time.monotonic() - 1
        assert aw._should_run_watchdog() is False
        aw._last_watchdog_run = time.monotonic() - 7200
        assert aw._should_run_watchdog() is True
    finally:
        aw._last_watchdog_run = original
