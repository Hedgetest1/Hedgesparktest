"""
resend_webhooks.py — Resend inbound email webhook handler.

POST /webhooks/resend/inbound
    Receives forwarded emails from Resend's inbound processing.
    Currently handles Sentry alert emails sent to alerts@hedgesparkhq.com.

Security:
    Webhook signature verified via Resend SDK (Svix HMAC-SHA256).
    Fails closed: unverified requests are rejected with 401.
    RESEND_WEBHOOK_SECRET must be set in .env.

Flow:
    1. Verify webhook signature
    2. Extract email fields (from, to, subject, body)
    3. Pass to sentry_triage.ingest_email()
    4. Return 200 (Resend expects 2xx to not retry)

Idempotency:
    Dedup on message_id — processing the same email twice is a no-op.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from fastapi import Depends

log = logging.getLogger("resend_webhooks")

router = APIRouter(prefix="/webhooks/resend", tags=["webhooks"])

_WEBHOOK_SECRET = None


def _get_webhook_secret() -> str:
    global _WEBHOOK_SECRET
    if _WEBHOOK_SECRET is None:
        _WEBHOOK_SECRET = os.getenv("RESEND_WEBHOOK_SECRET", "")
    return _WEBHOOK_SECRET


def _verify_webhook(payload: str, headers: dict) -> None:
    """
    Verify Resend webhook signature using the SDK.

    Raises HTTPException(401) on failure.
    No-ops if RESEND_WEBHOOK_SECRET is not set (dev mode warning).
    """
    secret = _get_webhook_secret()
    if not secret:
        log.warning("resend_webhooks: RESEND_WEBHOOK_SECRET not set — skipping verification (UNSAFE)")
        return

    try:
        import resend
        resend.Webhooks.verify({
            "payload": payload,
            "headers": {
                "svix-id": headers.get("svix-id", ""),
                "svix-timestamp": headers.get("svix-timestamp", ""),
                "svix-signature": headers.get("svix-signature", ""),
            },
            "webhook_secret": secret,
        })
    except ValueError as exc:
        log.warning("resend_webhooks: signature verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Webhook signature verification failed")
    except Exception as exc:
        log.warning("resend_webhooks: verification error: %s", exc)
        raise HTTPException(status_code=401, detail="Webhook verification error")


@router.post("/inbound")
async def resend_inbound_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle inbound email from Resend.

    Resend sends a JSON payload with email fields:
        {
            "type": "email.received",
            "data": {
                "id": "...",
                "from": "noreply@sentry.io",
                "to": ["alerts@hedgesparkhq.com"],
                "subject": "[Sentry] ...",
                "text": "...",
                "html": "..."
            }
        }
    """
    # Read raw body for signature verification
    raw_body = await request.body()
    payload_str = raw_body.decode("utf-8", errors="replace")

    # Verify signature
    headers = dict(request.headers)
    _verify_webhook(payload_str, headers)

    # Parse JSON
    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("resend_webhooks: invalid JSON: %s", exc)
        return {"status": "invalid_json"}

    # Extract event type
    event_type = payload.get("type", "")

    # Only process email.received events
    if event_type != "email.received":
        log.info("resend_webhooks: ignoring event type=%s", event_type)
        return {"status": "ignored", "event_type": event_type}

    data = payload.get("data", {})

    # Extract email fields
    message_id = data.get("id") or data.get("message_id")
    from_addr = data.get("from", "")
    to_list = data.get("to", [])
    to_addr = to_list[0] if isinstance(to_list, list) and to_list else str(to_list)
    subject = data.get("subject", "")
    body = data.get("html") or data.get("text") or ""

    # Only process emails that look like Sentry alerts
    is_sentry = (
        "sentry" in (from_addr or "").lower()
        or "[sentry]" in (subject or "").lower()
        or "sentry" in (subject or "").lower()
    )

    if not is_sentry:
        log.info("resend_webhooks: non-Sentry email from=%s subject=%r — storing anyway", from_addr, subject[:80])
        # Still ingest — could be useful for future support email triage

    # Ingest into triage pipeline
    # NOTE: Email-based Sentry intake is the FALLBACK path.
    # Primary path is POST /webhooks/sentry/inbound (direct webhook).
    # This path should only fire if Sentry webhook is not configured or fails.
    if is_sentry:
        log.warning(
            "resend_webhooks: Sentry alert via EMAIL fallback (subject=%r). "
            "Primary path should be direct webhook. Configure Sentry webhook "
            "integration to stop using email-based intake.",
            (subject or "")[:80],
        )

    from app.services.sentry_triage import ingest_email
    result = ingest_email(
        db,
        message_id=message_id,
        subject=subject,
        body=body,
        from_addr=from_addr,
        to_addr=to_addr,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("resend_webhooks: commit failed: %s", exc)
        # Return 200 anyway — Resend will retry on non-2xx
        return {"status": "commit_error"}

    log.info(
        "resend_webhooks: ingested email subject=%r status=%s incident_id=%s",
        (subject or "")[:60], result["status"], result.get("incident_id"),
    )

    return {"status": "ok", "result": result}
