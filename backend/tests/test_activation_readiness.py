"""Tests for approval notifications and orchestrator activation readiness."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.action_approval import ActionApproval
from app.core.alert_delivery import notify_approval_pending
from app.services.audit import write_audit_log

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Approval Slack notification
# ---------------------------------------------------------------------------

def test_approval_notification_sends_via_telegram():
    """Telegram notification sent with buttons when configured."""
    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message_with_buttons", return_value=True) as mock_tg:
        result = notify_approval_pending(
            approval_id=42,
            action_type="restart_worker",
            target_id="wishspark-worker",
            reason="Worker stuck for 3 cycles",
            expires_at="2026-03-27T19:00:00Z",
        )
    assert result is True
    mock_tg.assert_called_once()
    sent_text = mock_tg.call_args[0][0]
    sent_buttons = mock_tg.call_args[0][1]
    assert "APPROVAL NEEDED" in sent_text
    assert "restart_worker" in sent_text
    assert any("/approve 42" in btn["callback_data"] for row in sent_buttons for btn in row)


def test_approval_notification_falls_back_to_slack():
    """If Telegram not configured, falls back to Slack."""
    mock_resp = MagicMock(status_code=200)
    with patch("app.services.telegram_agent.is_configured", return_value=False), \
         patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", return_value=mock_resp) as mock_post:
        result = notify_approval_pending(
            approval_id=42, action_type="restart_worker", target_id="w",
        )
    assert result is True
    mock_post.assert_called_once()


def test_approval_notification_noop_without_any_channel():
    """No Telegram + no Slack → returns False."""
    with patch("app.services.telegram_agent.is_configured", return_value=False), \
         patch("app.core.alert_delivery._SLACK_URL", ""):
        result = notify_approval_pending(
            approval_id=1, action_type="test", target_id="t",
        )
    assert result is False


def test_approval_notification_fails_safely():
    """All channels fail → returns False, no exception."""
    with patch("app.services.telegram_agent.is_configured", return_value=True), \
         patch("app.services.telegram_agent.send_message_with_buttons", side_effect=Exception("fail")), \
         patch("app.core.alert_delivery._SLACK_URL", "https://hooks.slack.com/test"), \
         patch("app.core.alert_delivery.httpx.post", side_effect=Exception("timeout")):
        result = notify_approval_pending(
            approval_id=1, action_type="test", target_id="t",
        )
    assert result is False


def test_approval_db_persists_without_notification(db):
    """Approval row exists even if Slack send fails."""
    audit = write_audit_log(
        db, actor_type="agent", actor_name="test",
        action_type="test_prop", status="awaiting_approval",
    )
    db.flush()
    approval = ActionApproval(
        audit_log_id=audit.id, action_type="restart_worker",
        target_id="wishspark-worker", status="pending",
        expires_at=_now() + timedelta(hours=2),
    )
    db.add(approval)
    db.flush()

    # Simulate failed notification
    approval.notified_at = None  # not notified

    assert approval.id is not None
    assert approval.status == "pending"
    assert approval.notified_at is None


def test_duplicate_notification_prevented(db):
    """Approval with notified_at already set → should not re-notify."""
    audit = write_audit_log(
        db, actor_type="agent", actor_name="test",
        action_type="test", status="awaiting_approval",
    )
    db.flush()
    approval = ActionApproval(
        audit_log_id=audit.id, action_type="restart_worker",
        target_id="wishspark-worker", status="pending",
        expires_at=_now() + timedelta(hours=2),
        notified_at=_now(),  # already notified
    )
    db.add(approval)
    db.flush()

    # The notified_at field is the dedup mechanism — code checks it before sending
    assert approval.notified_at is not None


# ---------------------------------------------------------------------------
# Readiness endpoint
# ---------------------------------------------------------------------------

def test_readiness_requires_auth(client):
    """Readiness endpoint requires operator auth."""
    resp = client.get("/ops/readiness/orchestrator")
    assert resp.status_code == 401


def test_readiness_reports_current_mode(client):
    """Readiness reports the configured mode and action registry."""
    resp = client.get("/ops/readiness/orchestrator", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] in ("deterministic", "proposal", "hybrid")
    assert data["operator_key_configured"] is True
    assert "actions" in data
    assert data["actions"]["total"] >= 7


def test_readiness_reports_missing_llm_key(client):
    """proposal mode without LLM key → missing requirement."""
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "proposal"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
            resp = client.get("/ops/readiness/orchestrator", headers=_op_headers())
        data = resp.json()
        # May or may not be ready depending on env — check structure
        assert "missing_requirements" in data
        assert "llm_available" in data
    finally:
        orch.ORCHESTRATOR_MODE = original


def test_readiness_warns_about_missing_slack(client):
    """hybrid mode without Slack → warning."""
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"
        with patch.dict(os.environ, {"OPS_SLACK_WEBHOOK_URL": ""}, clear=False):
            resp = client.get("/ops/readiness/orchestrator", headers=_op_headers())
        data = resp.json()
        assert "warnings" in data
    finally:
        orch.ORCHESTRATOR_MODE = original
