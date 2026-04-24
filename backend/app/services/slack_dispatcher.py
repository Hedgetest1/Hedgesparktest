"""
slack_dispatcher.py — per-merchant Slack webhook integration.

Strada 3.5 (2026-04-20). Closes the "no Slack integration" gap at the
€39 tier. Merchants paste an incoming-webhook URL; we encrypt it at
rest (same AES-256-GCM as Shopify tokens) and push the daily brief to
their channel in addition to email.

Why just incoming webhooks: no OAuth app, no Slack marketplace review,
no bot-token rotation. The merchant creates a webhook in their own
Slack workspace, pastes it into HedgeSpark, done. Maximum simplicity
at the merchant's side (the founder's §1 doctrine: "easier than every
competitor").

Public interface:
    save_webhook(db, shop, webhook_url) -> (ok, error)
    disconnect(db, shop) -> None
    get_status(db, shop) -> dict
    post_message(db, shop, text, blocks=None) -> (ok, error)
    post_daily_brief(db, shop, brief) -> (ok, error)

Security:
  - Webhook URL encrypted at rest with merchant_token_encryption_key
    (same key as Shopify tokens). Never logged in plaintext.
  - Only URLs matching https://hooks.slack.com/services/... are
    accepted — rejects malformed or suspicious inputs before they
    touch the DB.
  - 10-second request timeout — Slack rarely takes more than 1s, so
    anything above 10s is a connectivity or DNS issue worth failing
    fast on.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token, encrypt_token
from app.models.merchant import Merchant

log = logging.getLogger("slack_dispatcher")

_WEBHOOK_PATTERN = re.compile(
    r"^https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+$"
)
_REQUEST_TIMEOUT_S = 10.0


def _validate_webhook(url: str) -> tuple[bool, str]:
    """Return (is_valid, error_reason)."""
    if not url or not isinstance(url, str):
        return False, "webhook URL is empty"
    url = url.strip()
    if len(url) > 500:
        return False, "webhook URL is too long"
    if not _WEBHOOK_PATTERN.match(url):
        return False, "invalid Slack webhook format (expected https://hooks.slack.com/services/T.../B.../XXX)"
    return True, ""


def save_webhook(db: Session, shop: str, webhook_url: str) -> tuple[bool, str]:
    """Validate, encrypt, and store a Slack incoming-webhook URL for
    the merchant. Does NOT test-post — callers should call post_message
    separately if they want the connect flow to confirm with a test.
    Returns (ok, error_message)."""
    ok, err = _validate_webhook(webhook_url)
    if not ok:
        return False, err

    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None:
        return False, "merchant not found"

    encrypted = encrypt_token(webhook_url.strip())
    m.slack_webhook_encrypted = encrypted
    m.slack_status = "connected"
    m.slack_last_error = None
    db.commit()
    log.info("slack_dispatcher: webhook saved for shop=%s", shop)
    return True, ""


def disconnect(db: Session, shop: str) -> None:
    """Remove the Slack webhook from the merchant. Idempotent — safe to
    call when nothing is configured."""
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None:
        return
    m.slack_webhook_encrypted = None
    m.slack_status = "not_connected"
    m.slack_last_error = None
    db.commit()
    log.info("slack_dispatcher: webhook removed for shop=%s", shop)


def get_status(db: Session, shop: str) -> dict[str, Any]:
    """Operator-shaped status — never returns the URL itself. Used by
    /merchant/slack/status and by the settings card to show
    connected/error/not_connected state + last error."""
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None:
        return {"connected": False, "status": "not_connected", "last_error": None}
    return {
        "connected": bool(m.slack_webhook_encrypted) and m.slack_status == "connected",
        "status": m.slack_status or "not_connected",
        "last_error": m.slack_last_error,
    }


def post_message(
    db: Session,
    shop: str,
    text: str,
    blocks: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, str]:
    """Post a message to the merchant's configured Slack channel.
    Returns (ok, error_message). On error, persists the sanitized
    message to slack_last_error so the settings UI can surface it."""
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None:
        return False, "merchant not found"
    if not m.slack_webhook_encrypted:
        return False, "Slack not connected"

    url = decrypt_token(m.slack_webhook_encrypted)
    if not url:
        return False, "webhook decryption failed"

    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = client.post(url, content=json.dumps(payload), headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            # Slack returns 'invalid_payload' / 'channel_not_found' /
            # etc as plain-text body. Truncate before storing so the
            # error column (255 chars) doesn't overflow.
            body = (resp.text or "").strip()[:220]
            err = f"slack HTTP {resp.status_code}: {body}"
            m.slack_status = "error"
            m.slack_last_error = err[:255]
            db.commit()
            log.warning("slack_dispatcher: post failed for %s: %s", shop, err)
            return False, err

        # Success — clear any prior error state.
        if m.slack_status != "connected" or m.slack_last_error:
            m.slack_status = "connected"
            m.slack_last_error = None
            db.commit()
        return True, ""

    except Exception as exc:
        # Best-effort error-state persist. The exception may itself be a
        # commit failure (line 148 / 156) — in that case the session is
        # dirty and any further ORM op raises PendingRollbackError, so
        # we rollback before retrying the error-state write. If THAT
        # commit also fails (DB down), we log and return without
        # masking the original failure to the caller.
        err = f"slack post error: {type(exc).__name__}: {exc}"
        try:
            db.rollback()
            m.slack_status = "error"
            m.slack_last_error = err[:255]
            db.commit()
        except Exception as inner_exc:
            try:
                db.rollback()
            except Exception:
                pass  # SILENT-EXCEPT-OK: rollback-of-rollback when DB itself is down
            log.error(
                "slack_dispatcher: error-state persist failed for %s: %s",
                shop, inner_exc,
            )
        log.warning("slack_dispatcher: exception for %s: %s", shop, exc)
        return False, err


def post_daily_brief(
    db: Session,
    shop: str,
    brief: dict[str, Any],
) -> tuple[bool, str]:
    """Format the daily brief as a Slack message and post it. Skipped
    silently when Slack isn't connected (so the caller can always
    invoke this without pre-checking)."""
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if m is None or not m.slack_webhook_encrypted:
        return True, ""  # silent no-op — not an error

    shop_name = shop.replace(".myshopify.com", "").replace("-", " ").title()
    signals_count = int(brief.get("signals_count") or 0)
    top_product = (brief.get("top_product_label") or "").strip()
    top_action = (brief.get("top_action") or "").strip()

    if signals_count == 0:
        text = f":white_check_mark: *HedgeSpark morning brief — {shop_name}*\nNo significant findings overnight. Funnel clean, tracker watching."
    else:
        plural = "s" if signals_count != 1 else ""
        lines = [f":spark: *HedgeSpark morning brief — {shop_name}*"]
        lines.append(f"*{signals_count}* finding{plural} today, ranked by economic impact.")
        if top_product:
            lines.append("")
            lines.append(f":arrow_forward: *Lead story:* {top_product}")
            if top_action:
                lines.append(f"   _Action suggested:_ {top_action}")
        lines.append("")
        lines.append("Full brief: https://app.hedgesparkhq.com/app/lite")
        text = "\n".join(lines)

    return post_message(db, shop, text)
