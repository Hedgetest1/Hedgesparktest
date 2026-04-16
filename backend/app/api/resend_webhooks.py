"""
resend_webhooks.py — Resend webhook handlers.

POST /webhooks/resend/inbound
    Receives forwarded emails from Resend's inbound processing.
    Handles Sentry alert emails sent to alerts@hedgesparkhq.com.

POST /webhooks/resend/events
    Receives Resend delivery events (delivered, opened, clicked, bounced, complained).
    Maps events to merchant_emails via resend_id, updates journey state.

POST /webhooks/resend/merchant-inbound
    Receives merchant replies to dev@hedgesparkhq.com / hello@hedgesparkhq.com.
    Classifies intent, routes to appropriate pipeline.

Security:
    Webhook signature verified via Resend SDK (Svix HMAC-SHA256).
    Fails closed: unverified requests are rejected with 401.
    RESEND_WEBHOOK_SECRET must be set in .env.

Idempotency:
    Dedup on message_id / resend_event_id — processing twice is a no-op.
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

    Raises HTTPException on failure:
      * 503 if `RESEND_WEBHOOK_SECRET` is unset — fail closed. Previously
        this path silently accepted every request, allowing anyone to
        inject inbound email events (email journey manipulation,
        fake alerts). The misconfiguration is now loud and refuses
        traffic until the secret is provisioned.
      * 401 if the signature is invalid or the SDK raises.
    """
    secret = _get_webhook_secret()
    if not secret:
        log.error(
            "resend_webhooks: RESEND_WEBHOOK_SECRET not set — refusing "
            "webhook traffic. Configure the secret in .env before "
            "reenabling Resend inbound routing."
        )
        raise HTTPException(
            status_code=503,
            detail="Webhook verification not configured",
        )

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
        from app.core.privacy import mask_email
        log.info(
            "resend_webhooks: non-Sentry email from=%s subject=%r — storing anyway",
            mask_email(from_addr), subject[:80],
        )
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


# ---------------------------------------------------------------------------
# Resend delivery events (delivered / opened / clicked / bounced / complained)
# ---------------------------------------------------------------------------

_TRACKED_EVENT_TYPES = {
    "email.delivered",
    "email.opened",
    "email.clicked",
    "email.bounced",
    "email.complained",
}


@router.post("/events")
async def resend_event_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Resend delivery event webhooks.

    Resend sends events like:
        {
            "type": "email.opened",
            "created_at": "2026-04-08T...",
            "data": {
                "email_id": "re_...",
                "to": ["merchant@example.com"],
                ...
            }
        }

    Flow:
        1. Verify webhook signature
        2. Parse event type and email_id
        3. Look up merchant_emails by resend_id to resolve shop_domain
        4. Store in email_events table (dedup on composite key)
        5. Update merchant journey state (opened/clicked)
    """
    raw_body = await request.body()
    payload_str = raw_body.decode("utf-8", errors="replace")

    headers = dict(request.headers)
    _verify_webhook(payload_str, headers)

    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("resend_events: invalid JSON: %s", exc)
        return {"status": "invalid_json"}

    event_type_raw = payload.get("type", "")

    if event_type_raw not in _TRACKED_EVENT_TYPES:
        return {"status": "ignored", "event_type": event_type_raw}

    # Normalize: "email.opened" → "opened"
    event_type = event_type_raw.replace("email.", "")

    data = payload.get("data", {})
    resend_email_id = data.get("email_id") or data.get("id") or ""
    to_list = data.get("to", [])
    to_email = to_list[0] if isinstance(to_list, list) and to_list else str(to_list) if to_list else None

    # Build a dedup key from event type + email id + timestamp
    event_created_at = payload.get("created_at")
    resend_event_id = f"{event_type}:{resend_email_id}:{event_created_at or ''}"

    # Store event
    from app.models.email_event import EmailEvent
    from app.models.merchant_email import MerchantEmail as MerchantEmailModel

    # Dedup check
    existing = (
        db.query(EmailEvent.id)
        .filter(EmailEvent.resend_event_id == resend_event_id)
        .first()
    )
    if existing:
        return {"status": "duplicate", "event_type": event_type}

    # Resolve shop_domain from merchant_emails (primary) or journey state (fallback)
    shop_domain = None
    email_type_resolved = None
    if resend_email_id:
        me = (
            db.query(MerchantEmailModel)
            .filter(MerchantEmailModel.resend_id == resend_email_id)
            .first()
        )
        if me:
            shop_domain = me.shop_domain
            email_type_resolved = me.email_type
        else:
            # Fallback: merchant_emails row may not exist yet (race between
            # followup_worker commit and Resend webhook delivery). Check
            # journey state which stores resend_ids for invite + followup.
            from app.models.merchant_journey_state import MerchantJourneyState
            from sqlalchemy import or_
            j = (
                db.query(MerchantJourneyState)
                .filter(or_(
                    MerchantJourneyState.beta_invite_resend_id == resend_email_id,
                    MerchantJourneyState.followup_48h_resend_id == resend_email_id,
                ))
                .first()
            )
            if j:
                shop_domain = j.shop_domain
                if j.beta_invite_resend_id == resend_email_id:
                    email_type_resolved = "beta_welcome"
                elif j.followup_48h_resend_id == resend_email_id:
                    email_type_resolved = j.followup_48h_variant

    # Parse event timestamp
    event_ts = None
    if event_created_at:
        try:
            from datetime import datetime
            # Resend timestamps: "2026-04-08T12:00:00.000Z"
            event_ts = datetime.fromisoformat(event_created_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError):
            pass

    event = EmailEvent(
        resend_email_id=resend_email_id,
        event_type=event_type,
        to_email=to_email,
        shop_domain=shop_domain,
        email_type=email_type_resolved,
        event_timestamp=event_ts,
        resend_event_id=resend_event_id,
        raw_payload=payload_str[:4000],  # cap storage
    )
    db.add(event)

    # Update journey state for opened/clicked events
    if shop_domain and event_type in ("opened", "clicked"):
        try:
            from app.services.email_journey import record_event
            record_event(db, shop_domain, event_type, resend_email_id, event_timestamp=event_ts)
        except Exception as exc:
            log.warning("resend_events: journey update failed shop=%s event=%s: %s", shop_domain, event_type, exc)

    # Record in email performance memory (self-improving loop)
    if shop_domain and email_type_resolved and event_type in ("opened", "clicked", "complained"):
        try:
            from app.services.email_performance import record_email_event
            record_email_event(db, shop_domain, email_type_resolved, event_type)
        except Exception as exc:
            log.warning("resend_events: performance tracking failed: %s", exc)

    # Suppress future sends on hard bounce or complaint — both Redis (fast) and DB (durable)
    if shop_domain and event_type in ("bounced", "complained"):
        try:
            from app.core.redis_client import _client
            rc = _client()
            if rc is not None:
                rc.set(f"hs:email_suppressed:{shop_domain}", event_type, ex=86400 * 90)
        except Exception as exc:
            log.warning("resend_webhooks: resend_event_webhook failed: %s", exc)
        try:
            from app.services.email_journey import suppress_email
            suppress_email(db, shop_domain, event_type)
            log.warning(
                "resend_events: email %s for shop=%s — future sends suppressed (DB+Redis)",
                event_type, shop_domain,
            )
        except Exception as exc:
            log.warning("resend_events: DB suppression failed: %s", exc)

        # Feed deliverability problems into the self-healing pipeline.
        # Bounce/complaint signals deliverability bugs (broken templates,
        # bad sender reputation, wrong addresses) — exactly what the
        # generic Rule 7 catch-all is built to triage.
        try:
            from app.services.alerting import write_alert
            severity = "critical" if event_type == "complained" else "warning"
            write_alert(
                db,
                source=f"resend:{email_type_resolved or 'unknown'}",
                alert_type=f"email_{event_type}",
                severity=severity,
                shop_domain=shop_domain,
                summary=(
                    f"Email {event_type} for {to_email or shop_domain} "
                    f"(type={email_type_resolved or 'unknown'})"
                ),
                detail={
                    "event_type": event_type,
                    "to_email": to_email,
                    "email_type": email_type_resolved,
                    "resend_id": resend_email_id,
                },
            )
        except Exception as exc:
            log.debug("resend_events: write_alert failed (non-fatal): %s", exc)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("resend_events: commit failed: %s", exc)
        return {"status": "commit_error"}

    log.info(
        "resend_events: stored event=%s email_id=%s shop=%s",
        event_type, resend_email_id[:20] if resend_email_id else "?", shop_domain,
    )
    return {"status": "ok", "event_type": event_type, "shop_domain": shop_domain}


# ---------------------------------------------------------------------------
# Merchant inbound replies (dev@hedgesparkhq.com, hello@hedgesparkhq.com)
# ---------------------------------------------------------------------------

_MERCHANT_INBOUND_ADDRESSES = {
    "dev@hedgesparkhq.com",
    "hello@hedgesparkhq.com",
}


@router.post("/merchant-inbound")
async def resend_merchant_inbound_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle inbound merchant replies.

    Same payload format as /inbound but routed differently:
    - Identifies merchant by sender email
    - Classifies intent (bug, feature, complaint, etc.)
    - Routes to appropriate pipeline
    - Updates journey state
    """
    raw_body = await request.body()
    payload_str = raw_body.decode("utf-8", errors="replace")

    headers = dict(request.headers)
    _verify_webhook(payload_str, headers)

    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("resend_merchant_inbound: invalid JSON: %s", exc)
        return {"status": "invalid_json"}

    event_type = payload.get("type", "")
    if event_type != "email.received":
        return {"status": "ignored", "event_type": event_type}

    data = payload.get("data", {})

    message_id = data.get("id") or data.get("message_id")
    from_addr = data.get("from", "")
    to_list = data.get("to", [])
    to_addr = to_list[0] if isinstance(to_list, list) and to_list else str(to_list)
    subject = data.get("subject", "")
    body_text = data.get("text")
    body_html = data.get("html")

    from app.services.inbound_email_processor import process_inbound
    result = process_inbound(
        db,
        message_id=message_id,
        from_email=from_addr,
        to_email=to_addr,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("resend_merchant_inbound: commit failed: %s", exc)
        return {"status": "commit_error"}

    log.info(
        "resend_merchant_inbound: result=%s from=%s classification=%s",
        result["status"], from_addr[:40], result.get("classification"),
    )

    return {"status": "ok", "result": result}
