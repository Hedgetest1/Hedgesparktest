"""
notifier_guard — hard env gate for outbound notifications.

These tests prove real Telegram/Slack sends are IMPOSSIBLE from tests,
even when callers forget to mock the sender. The guard fires before
any HTTP egress.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from app.core import notifier_guard


def _set_env(monkeypatch, app_env: str | None, allow_real: str | None = None):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    if allow_real is None:
        monkeypatch.delenv("NOTIFICATIONS_ALLOW_REAL", raising=False)
    else:
        monkeypatch.setenv("NOTIFICATIONS_ALLOW_REAL", allow_real)


# ---------------------------------------------------------------------------
# Guard primitives
# ---------------------------------------------------------------------------

def test_guard_allows_production(monkeypatch):
    _set_env(monkeypatch, "production")
    assert notifier_guard.is_real_send_allowed() is True


def test_guard_allows_prod_alias(monkeypatch):
    _set_env(monkeypatch, "prod")
    assert notifier_guard.is_real_send_allowed() is True


def test_guard_blocks_test(monkeypatch):
    _set_env(monkeypatch, "test")
    assert notifier_guard.is_real_send_allowed() is False


def test_guard_blocks_staging(monkeypatch):
    _set_env(monkeypatch, "staging")
    assert notifier_guard.is_real_send_allowed() is False


def test_guard_blocks_development(monkeypatch):
    _set_env(monkeypatch, "development")
    assert notifier_guard.is_real_send_allowed() is False


def test_guard_blocks_when_unset(monkeypatch):
    """Fail-safe: unset APP_ENV blocks sends."""
    _set_env(monkeypatch, None)
    assert notifier_guard.is_real_send_allowed() is False


def test_guard_blocks_empty(monkeypatch):
    _set_env(monkeypatch, "")
    assert notifier_guard.is_real_send_allowed() is False


def test_override_allows_real(monkeypatch):
    _set_env(monkeypatch, "staging", "1")
    assert notifier_guard.is_real_send_allowed() is True


def test_override_true_allows_real(monkeypatch):
    _set_env(monkeypatch, "test", "true")
    assert notifier_guard.is_real_send_allowed() is True


def test_override_zero_still_blocks(monkeypatch):
    _set_env(monkeypatch, "test", "0")
    assert notifier_guard.is_real_send_allowed() is False


def test_require_production_returns_false_when_blocked(monkeypatch, caplog):
    _set_env(monkeypatch, "test")
    with caplog.at_level("INFO", logger="notifier_guard"):
        assert notifier_guard.require_production("telegram", "hello") is False
    assert any("BLOCKED" in r.message for r in caplog.records)


def test_require_production_returns_true_in_prod(monkeypatch):
    _set_env(monkeypatch, "production")
    assert notifier_guard.require_production("telegram", "hello") is True


# ---------------------------------------------------------------------------
# Telegram send paths — guarded
# ---------------------------------------------------------------------------

def test_telegram_send_message_blocked_in_test(monkeypatch):
    """send_message refuses real HTTP when APP_ENV != production."""
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "fake-token")
    monkeypatch.setattr("app.services.telegram_agent._CHAT_ID", "123")

    fake_http = MagicMock()
    with patch("app.services.telegram_agent._get_http_client", return_value=fake_http):
        from app.services.telegram_agent import send_message
        result = send_message("DANGEROUS — would leak in prod")

    assert result is False
    fake_http.post.assert_not_called()


def test_telegram_send_with_buttons_blocked_in_test(monkeypatch):
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "fake-token")
    monkeypatch.setattr("app.services.telegram_agent._CHAT_ID", "123")

    fake_http = MagicMock()
    with patch("app.services.telegram_agent._get_http_client", return_value=fake_http):
        from app.services.telegram_agent import send_message_with_buttons
        result = send_message_with_buttons(
            "DANGEROUS",
            [[{"text": "x", "callback_data": "/x"}]],
        )

    assert result is False
    fake_http.post.assert_not_called()


def test_telegram_warmup_blocked_in_test(monkeypatch):
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "fake-token")

    fake_http = MagicMock()
    with patch("app.services.telegram_agent._get_http_client", return_value=fake_http):
        from app.services.telegram_agent import warmup_connection
        warmup_connection()

    fake_http.get.assert_not_called()
    fake_http.post.assert_not_called()


def test_telegram_register_bot_commands_blocked_in_test(monkeypatch):
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "fake-token")

    fake_http = MagicMock()
    with patch("app.services.telegram_agent._get_http_client", return_value=fake_http):
        from app.services.telegram_agent import register_bot_commands
        result = register_bot_commands()

    assert result is False
    fake_http.post.assert_not_called()


def test_telegram_send_message_allowed_in_production(monkeypatch):
    """Guard does NOT block legitimate production sends."""
    _set_env(monkeypatch, "production")
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "fake-token")
    monkeypatch.setattr("app.services.telegram_agent._CHAT_ID", "123")

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"result": {"message_id": 42}}
    fake_http = MagicMock()
    fake_http.post.return_value = fake_resp

    with patch("app.services.telegram_agent._get_http_client", return_value=fake_http):
        from app.services.telegram_agent import send_message
        result = send_message("legit prod alert")

    assert result == 42
    fake_http.post.assert_called_once()


# ---------------------------------------------------------------------------
# Slack send paths — guarded
# ---------------------------------------------------------------------------

def test_slack_deliver_alert_blocked_in_test(monkeypatch):
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/fake")

    fake_post = MagicMock()
    with patch("app.core.alert_delivery.httpx.post", fake_post):
        from app.core.alert_delivery import deliver_alert_externally
        sent = deliver_alert_externally(
            severity="critical",
            source="test_source",
            alert_type="gdpr_failure",
            summary="TEST — must never send",
        )

    assert sent is False
    fake_post.assert_not_called()


def test_slack_approval_fallback_blocked_in_test(monkeypatch):
    """When Telegram fails AND APP_ENV=test, Slack fallback is ALSO blocked."""
    _set_env(monkeypatch, "test")
    monkeypatch.setattr("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/fake")

    fake_post = MagicMock()
    with patch("app.core.alert_delivery.httpx.post", fake_post), \
         patch("app.services.telegram_agent.is_configured", return_value=False):
        from app.core.alert_delivery import notify_approval_pending
        sent = notify_approval_pending(
            approval_id=1, action_type="test_action", target_id="t1",
        )

    assert sent is False
    fake_post.assert_not_called()


def test_slack_deliver_alert_allowed_in_production(monkeypatch):
    _set_env(monkeypatch, "production")
    monkeypatch.setattr("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/fake")

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_post = MagicMock(return_value=fake_resp)

    with patch("app.core.alert_delivery.httpx.post", fake_post):
        from app.core.alert_delivery import deliver_alert_externally
        sent = deliver_alert_externally(
            severity="critical",
            source="prod_source",
            alert_type="gdpr_failure",
            summary="legit prod alert",
        )

    assert sent is True
    fake_post.assert_called_once()


# ---------------------------------------------------------------------------
# Conftest enforcement — APP_ENV must be "test" for all tests
# ---------------------------------------------------------------------------

def test_conftest_forces_app_env_test():
    """Proves the conftest-level override actually pinned APP_ENV."""
    assert os.environ.get("APP_ENV") == "test"
    assert notifier_guard.is_real_send_allowed() is False


def test_conftest_clears_allow_real_override():
    """NOTIFICATIONS_ALLOW_REAL is unset in tests — no bypass."""
    assert os.environ.get("NOTIFICATIONS_ALLOW_REAL") is None
