"""Safe cleanup — two-step confirmation + /cleanup_safe + audit log."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from tests.conftest import SHOP_A


AUTHORIZED_CHAT = "123456789"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setattr("app.services.telegram_agent._CHAT_ID", AUTHORIZED_CHAT)
    monkeypatch.setattr("app.services.telegram_agent._BOT_TOKEN", "test-token")
    # Rate limiting is entirely in telegram_safety (Redis-backed) — the
    # telegram_agent-local reset_rate_limits was deleted as dead code
    # 2026-04-23. Only the telegram_safety reset is needed.
    from app.core.telegram_safety import reset_rate_limits as reset_safety
    reset_safety()


@pytest.fixture
def _fake_redis(monkeypatch):
    """In-memory redis stand-in for cleanup pending state."""
    class FakeRedis:
        def __init__(self):
            self.store: dict = {}
        def set(self, k, v, ex=None, nx=False):
            if nx and k in self.store:
                return None
            self.store[k] = v
            return True
        def get(self, k):
            return self.store.get(k)
        def delete(self, k):
            self.store.pop(k, None)
            return 1
        def incr(self, k):
            self.store[k] = int(self.store.get(k, 0)) + 1
            return self.store[k]
        def expire(self, k, ttl):
            return True
        def scan_iter(self, match=None, count=100):
            return iter(list(self.store.keys()))

    fake = FakeRedis()
    import app.core.redis_client as rc_mod
    monkeypatch.setattr(rc_mod, "_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Two-step confirmation
# ---------------------------------------------------------------------------

def test_cleanup_first_call_requires_confirmation(db, _fake_redis):
    """First /cleanup returns warning + pending state, does NOT mutate."""
    db.add(OpsAlert(
        severity="warning", source="t", alert_type="pre_clean",
        summary="keep me", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.commit()

    before = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
    )).scalar()
    assert before >= 1

    with patch("app.services.telegram_agent.send_message_with_buttons",
               return_value=True):
        from app.services.telegram_agent import _cmd_cleanup
        _cmd_cleanup(db, chat_id=AUTHORIZED_CHAT)

    # Alert NOT yet resolved
    after = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
    )).scalar()
    assert after == before, "first /cleanup MUST NOT mutate"

    # Pending state recorded in Redis
    pending = _fake_redis.get(f"hs:cleanup_pending:{AUTHORIZED_CHAT}")
    assert pending is not None, "pending state must be stored"


def test_cleanup_confirm_executes_after_first_call(db, _fake_redis):
    """Second step (/cleanup_confirm) actually resolves alerts + audit logs."""
    db.add(OpsAlert(
        severity="warning", source="t", alert_type="to_clean",
        summary="resolve me", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.commit()

    from app.services.telegram_agent import _cmd_cleanup, _cmd_cleanup_confirm

    # Step 1: stage confirmation
    with patch("app.services.telegram_agent.send_message_with_buttons",
               return_value=True):
        _cmd_cleanup(db, chat_id=AUTHORIZED_CHAT)

    # Step 2: confirm → executes
    import logging
    with patch("app.services.telegram_agent.log") as mock_log:
        result = _cmd_cleanup_confirm(db, chat_id=AUTHORIZED_CHAT)

    assert "Cleanup complete" in result
    assert "full" in result  # scope
    # Alert is now resolved
    remaining = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
    )).scalar()
    assert remaining == 0

    # Audit log written — call uses lazy %-formatting
    audit_calls = [
        c for c in mock_log.warning.call_args_list
        if "AUDIT cleanup" in (c.args[0] if c.args else "")
    ]
    assert audit_calls, "audit log entry missing"
    # Render the log line with its args and assert on the result
    call = audit_calls[0]
    rendered = call.args[0] % call.args[1:]
    assert "scope=full" in rendered
    assert f"actor_chat={AUTHORIZED_CHAT}" in rendered


def test_cleanup_confirm_without_pending_fails(db, _fake_redis):
    """/cleanup_confirm without prior /cleanup returns error, no mutation."""
    db.add(OpsAlert(
        severity="critical", source="t", alert_type="untouchable",
        summary="don't touch", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.commit()

    from app.services.telegram_agent import _cmd_cleanup_confirm
    result = _cmd_cleanup_confirm(db, chat_id=AUTHORIZED_CHAT)

    assert "No pending cleanup" in result or "expired" in result
    # Nothing resolved
    remaining = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts WHERE resolved = false"
    )).scalar()
    assert remaining >= 1


def test_cleanup_cancel_clears_pending_state(db, _fake_redis):
    """/cleanup_cancel removes pending state."""
    from app.services.telegram_agent import (
        _cmd_cleanup, _cmd_cleanup_cancel, _cmd_cleanup_confirm,
    )

    db.add(OpsAlert(
        severity="warning", source="t", alert_type="cancel_me",
        summary="x", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.commit()

    with patch("app.services.telegram_agent.send_message_with_buttons",
               return_value=True):
        _cmd_cleanup(db, chat_id=AUTHORIZED_CHAT)
    assert _fake_redis.get(f"hs:cleanup_pending:{AUTHORIZED_CHAT}") is not None

    _cmd_cleanup_cancel(db, chat_id=AUTHORIZED_CHAT)
    assert _fake_redis.get(f"hs:cleanup_pending:{AUTHORIZED_CHAT}") is None

    # Subsequent /cleanup_confirm is rejected
    result = _cmd_cleanup_confirm(db, chat_id=AUTHORIZED_CHAT)
    assert "No pending cleanup" in result


# ---------------------------------------------------------------------------
# /cleanup_safe
# ---------------------------------------------------------------------------

def test_cleanup_safe_does_not_touch_critical_alerts(db, _fake_redis):
    """Safe cleanup preserves critical alerts and fresh incidents."""
    db.add(OpsAlert(
        severity="critical", source="t", alert_type="critical_live",
        summary="do not touch", shop_domain=SHOP_A, created_at=_now(),
    ))
    db.commit()

    from app.services.telegram_agent import _cmd_cleanup_safe
    _cmd_cleanup_safe(db, chat_id=AUTHORIZED_CHAT)

    still_there = db.execute(text(
        "SELECT COUNT(*) FROM ops_alerts "
        "WHERE resolved = false AND severity = 'critical'"
    )).scalar()
    assert still_there >= 1, "/cleanup_safe MUST NOT resolve critical alerts"


def test_cleanup_safe_writes_audit_log(db, _fake_redis):
    """/cleanup_safe writes structured audit log."""
    from app.services.telegram_agent import _cmd_cleanup_safe
    with patch("app.services.telegram_agent.log") as mock_log:
        _cmd_cleanup_safe(db, chat_id=AUTHORIZED_CHAT)

    audit_calls = [
        c for c in mock_log.warning.call_args_list
        if "AUDIT cleanup" in (c.args[0] if c.args else "")
    ]
    assert audit_calls
    rendered = audit_calls[0].args[0] % audit_calls[0].args[1:]
    assert "scope=safe" in rendered
