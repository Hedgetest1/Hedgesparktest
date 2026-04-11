"""
alert_delivery.py — External alert delivery (Slack webhook).

Sends structured alert notifications to a Slack incoming webhook.
Fails silently when:
  - OPS_SLACK_WEBHOOK_URL is not configured (no-op mode)
  - The webhook POST fails (alert already persisted in DB)
  - httpx times out or raises any exception

This is a fire-and-forget delivery layer. The ops_alerts DB table is
the source of truth — external delivery is a convenience, not a guarantee.

Configuration:
    OPS_SLACK_WEBHOOK_URL  — Slack incoming webhook URL
                             Not set = silent no-op (DB-only alerts)
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

_SLACK_URL: str = os.getenv("OPS_SLACK_WEBHOOK_URL", "").strip()
_TIMEOUT = 5.0

# Alert types that warrant external delivery
_EXTERNAL_ALERT_TYPES = frozenset({
    "gdpr_failure",
    "webhook_repair_failed",
    "worker_repeated_failure",
})

# Severities that always get external delivery regardless of type
_EXTERNAL_SEVERITIES = frozenset({"critical"})


def deliver_alert_externally(
    severity: str,
    source: str,
    alert_type: str,
    summary: str,
    shop_domain: str | None = None,
) -> bool:
    """
    Attempt external delivery of an alert. Returns True if delivered.

    Returns False (never raises) when:
      - No webhook configured
      - Alert type not eligible for external delivery
      - HTTP request fails
    """
    if not _SLACK_URL:
        return False

    # Only deliver high-value alerts externally
    if alert_type not in _EXTERNAL_ALERT_TYPES and severity not in _EXTERNAL_SEVERITIES:
        return False

    emoji = {"critical": ":red_circle:", "warning": ":large_orange_diamond:", "info": ":white_circle:"}.get(severity, ":grey_question:")
    shop_line = f"\n*Shop:* `{shop_domain}`" if shop_domain else ""

    payload = {
        "text": f"{emoji} *[{severity.upper()}]* {alert_type}{shop_line}\n{summary}\n_Source: {source}_",
    }

    # Guard: block real Slack sends in non-production environments.
    from app.core.notifier_guard import require_production
    if not require_production("slack", summary):
        return False

    try:
        resp = httpx.post(_SLACK_URL, json=payload, timeout=_TIMEOUT)
        if resp.status_code == 200:
            log.info("alert_delivery: sent to Slack alert_type=%s", alert_type)
            return True
        log.warning("alert_delivery: Slack returned %d for alert_type=%s", resp.status_code, alert_type)
        return False
    except Exception as exc:
        log.warning("alert_delivery: Slack send failed alert_type=%s: %s", alert_type, type(exc).__name__)
        return False


def notify_approval_pending(
    approval_id: int,
    action_type: str,
    target_id: str | None,
    shop_domain: str | None = None,
    reason: str | None = None,
    expires_at: str | None = None,
) -> bool:
    """
    Send Telegram notification (with button) for a pending TIER_1 approval.
    Falls back to Slack if Telegram not configured.

    Returns True if sent via any channel.
    """
    sent = False

    # Primary: Telegram with tappable Approve button
    try:
        from app.services.telegram_agent import send_message_with_buttons, is_configured
        if is_configured():
            shop_line = f"\nShop: {shop_domain}" if shop_domain else ""
            reason_line = f"\nReason: {reason}" if reason else ""
            expires_line = f"\nExpires: {expires_at[:16]}" if expires_at else ""

            msg = (
                f"*APPROVAL NEEDED — {action_type}*\n\n"
                f"Target: {target_id or 'N/A'}{shop_line}{reason_line}{expires_line}\n"
                f"Approval ID: {approval_id}\n\n"
                f"Tap to approve and execute:"
            )
            buttons = [
                [{"text": f"Approve & Execute #{approval_id}", "callback_data": f"/approve {approval_id}"}],
            ]
            sent = send_message_with_buttons(msg, buttons)
            if sent:
                log.info("alert_delivery: approval Telegram sent id=%d", approval_id)
    except Exception as exc:
        log.debug("alert_delivery: Telegram approval failed id=%d: %s", approval_id, exc)

    # Fallback: Slack (if configured and Telegram didn't work)
    if not sent and _SLACK_URL:
        # Guard: block real Slack sends in non-production environments.
        from app.core.notifier_guard import is_real_send_allowed as _slack_ok
        if not _slack_ok():
            from app.core.notifier_guard import block_send
            block_send("slack", f"approval#{approval_id}")
            return False
        try:
            shop_line = f"\n*Shop:* `{shop_domain}`" if shop_domain else ""
            reason_line = f"\n*Reason:* {reason}" if reason else ""
            expires_line = f"\n*Expires:* {expires_at}" if expires_at else ""

            payload = {
                "text": (
                    f":hourglass: *APPROVAL NEEDED* — `{action_type}`\n"
                    f"*Target:* `{target_id or 'N/A'}`{shop_line}{reason_line}{expires_line}\n"
                    f"*Approval ID:* {approval_id}\n"
                    f"_Approve via Telegram: /approve {approval_id}_"
                ),
            }
            resp = httpx.post(_SLACK_URL, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 200:
                log.info("alert_delivery: approval Slack sent id=%d", approval_id)
                sent = True
        except Exception as exc:
            log.warning("alert_delivery: approval Slack failed id=%d: %s", approval_id, type(exc).__name__)

    return sent
