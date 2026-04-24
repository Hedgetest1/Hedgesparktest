"""
sentry_webhooks.py — Native Sentry webhook handler.

POST /webhooks/sentry/inbound
    Receives Sentry issue alert webhooks (JSON) directly from Sentry.
    Bypasses email parsing — structured data, no regex fragility.

Security:
    Sentry signs webhooks with a shared secret (SENTRY_WEBHOOK_SECRET).
    HMAC-SHA256 verification on the raw body.
    Fails closed: unverified requests are rejected with 401.

    If SENTRY_WEBHOOK_SECRET is not set, verification is skipped
    with a warning (dev mode only — not production-safe).

Flow:
    1. Verify Sentry webhook signature
    2. Extract structured issue/event data
    3. Pass to sentry_triage.ingest_webhook()
    4. Return 200 (Sentry retries on non-2xx)

Idempotency:
    Dedup on sentry event_id — processing the same event twice is a no-op.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db

log = logging.getLogger("sentry_webhooks")

router = APIRouter(prefix="/webhooks/sentry", tags=["webhooks"])

_WEBHOOK_SECRET: str | None = None


def _get_webhook_secret() -> str:
    global _WEBHOOK_SECRET
    if _WEBHOOK_SECRET is None:
        _WEBHOOK_SECRET = os.getenv("SENTRY_WEBHOOK_SECRET", "")
    return _WEBHOOK_SECRET


def _verify_sentry_signature(payload: bytes, signature: str) -> None:
    """
    Verify Sentry webhook signature.

    Sentry sends: sentry-hook-signature header = HMAC-SHA256(secret, body).
    """
    secret = _get_webhook_secret()
    if not secret:
        log.warning("sentry_webhooks: SENTRY_WEBHOOK_SECRET not set — skipping verification (UNSAFE)")
        return

    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        log.warning("sentry_webhooks: signature verification failed")
        raise HTTPException(status_code=401, detail="Webhook signature verification failed")


@router.post("/inbound")  # test-exempt: webhook-receiver
async def sentry_inbound_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle native Sentry issue alert webhook.

    Sentry sends JSON payloads with structure:
        {
            "action": "triggered",
            "data": {
                "issue": { ... },
                "event": { ... }
            },
            "actor": { ... }
        }

    Only processes action=triggered events (new/regressed issues).
    Resolved events are logged but not ingested.
    """
    # Read raw body for signature verification
    raw_body = await request.body()

    # Verify signature
    signature = request.headers.get("sentry-hook-signature", "")
    _verify_sentry_signature(raw_body, signature)

    # Parse JSON
    try:
        payload = json.loads(raw_body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("sentry_webhooks: invalid JSON: %s", exc)
        return {"status": "invalid_json"}

    # Extract action
    action = payload.get("action", "")
    resource = request.headers.get("sentry-hook-resource", "")

    # Only process triggered issue alerts (new/regressed errors)
    if action not in ("triggered", "created"):
        log.info("sentry_webhooks: ignoring action=%s resource=%s", action, resource)
        return {"status": "ignored", "action": action}

    # Extract event ID for dedup
    data = payload.get("data", {})
    event = data.get("event", {})
    issue = data.get("issue", {})
    sentry_event_id = event.get("event_id") or issue.get("id")

    # Ingest into triage pipeline
    from app.services.sentry_triage import ingest_webhook
    result = ingest_webhook(
        db,
        payload=payload,
        sentry_event_id=str(sentry_event_id) if sentry_event_id else None,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("sentry_webhooks: commit failed: %s", exc)
        return {"status": "commit_error"}

    log.info(
        "sentry_webhooks: ingested event=%s status=%s incident_id=%s",
        sentry_event_id, result["status"], result.get("incident_id"),
    )

    return {"status": "ok", "result": result}
