"""
outbound_webhooks.py — Phase Ω ecosystem #1.

Publish events to merchant-configured outbound webhooks. Standard
exponential backoff, HMAC signing, idempotency keys, dead-letter on
N consecutive failures.

Call from anywhere with:

    from app.services.outbound_webhooks import publish_event
    publish_event(db, shop_domain, "nudge.fired", {"id": 42, "...": ...})

The publish call enqueues delivery rows and triggers immediate sends
synchronously (best-effort). A worker can later sweep `pending` rows
to retry — see deliver_pending_batch().
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.outbound_webhook import (
    OutboundWebhookDelivery,
    OutboundWebhookSubscription,
)

log = logging.getLogger("outbound_webhooks")

_MAX_ATTEMPTS = 6
_BACKOFF_SECONDS = (10, 60, 300, 1800, 7200, 21600)  # 10s, 1m, 5m, 30m, 2h, 6h
_AUTO_DISABLE_AFTER = 20  # consecutive failures
_HTTP_TIMEOUT_SECONDS = 8.0
_USER_AGENT = "HedgeSpark-Webhook/1.0"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------


def generate_secret() -> str:
    """A 64-char hex secret for HMAC signing."""
    return secrets.token_hex(32)


def create_subscription(
    db: Session,
    shop_domain: str,
    target_url: str,
    event_types: list[str],
    *,
    description: str | None = None,
    created_by: str | None = None,
) -> OutboundWebhookSubscription:
    sub = OutboundWebhookSubscription(
        shop_domain=shop_domain,
        target_url=target_url,
        secret=generate_secret(),
        event_types=event_types,
        description=description,
        created_by=created_by,
        status="active",
    )
    db.add(sub)
    db.flush()
    return sub


def revoke_subscription(db: Session, shop_domain: str, sub_id: int) -> bool:
    sub = (
        db.query(OutboundWebhookSubscription)
        .filter(
            OutboundWebhookSubscription.id == sub_id,
            OutboundWebhookSubscription.shop_domain == shop_domain,
        )
        .one_or_none()
    )
    if not sub:
        return False
    sub.status = "disabled"
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """
    Compute HMAC-SHA256 over `timestamp.body` using `secret`. The timestamp
    is included to defend against replay (clients enforce a 5-min skew).
    Hex digest (lowercase). Returns just the hex — header form is
    `t={ts},sig={hex}` (Stripe-style) handled by the delivery code.
    """
    msg = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def build_signature_header(secret: str, body: bytes) -> tuple[str, str]:
    """Return (timestamp_iso, header_value) ready for the X-HedgeSpark-Signature header."""
    ts = _now().isoformat()
    sig = sign_payload(secret, body, ts)
    return ts, f"t={ts},sig={sig}"


# ---------------------------------------------------------------------------
# Publish + deliver
# ---------------------------------------------------------------------------


def publish_event(
    db: Session,
    shop_domain: str,
    event_type: str,
    payload: dict,
    *,
    deliver_now: bool = True,
) -> list[int]:
    """
    Enqueue delivery rows for every active subscription that listens to
    this event_type, then attempt immediate delivery. Returns the list of
    delivery IDs created.
    """
    subs = (
        db.query(OutboundWebhookSubscription)
        .filter(
            OutboundWebhookSubscription.shop_domain == shop_domain,
            OutboundWebhookSubscription.status == "active",
        )
        .all()
    )
    if not subs:
        return []

    event_id = uuid.uuid4().hex
    delivery_ids: list[int] = []
    for sub in subs:
        wanted = sub.event_types or []
        if wanted and event_type not in wanted and "*" not in wanted:
            continue
        d = OutboundWebhookDelivery(
            subscription_id=sub.id,
            shop_domain=shop_domain,
            event_type=event_type,
            event_id=event_id,
            payload=payload,
            status="pending",
        )
        db.add(d)
        db.flush()
        delivery_ids.append(d.id)

    if deliver_now:
        for did in delivery_ids:
            try:
                attempt_delivery(db, did)
            except Exception as exc:
                log.debug("outbound_webhooks: deliver-now %s failed: %s", did, exc)

    return delivery_ids


def attempt_delivery(db: Session, delivery_id: int) -> str:
    """
    Send one delivery. Returns the resulting status string.
    Does NOT raise — failures are recorded in the row.
    """
    d = db.query(OutboundWebhookDelivery).get(delivery_id)
    if not d:
        return "missing"
    if d.status == "delivered":
        return "delivered"
    sub = db.query(OutboundWebhookSubscription).get(d.subscription_id)
    if not sub or sub.status != "active":
        d.status = "dead"
        d.last_attempted_at = _now()
        db.flush()
        return "dead"

    body = json.dumps({
        "id": d.event_id,
        "type": d.event_type,
        "shop_domain": d.shop_domain,
        "data": d.payload,
        "created_at": d.created_at.isoformat() if d.created_at else _now().isoformat(),
    }, default=str).encode()

    ts, sig_header = build_signature_header(sub.secret, body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
        "X-HedgeSpark-Event": d.event_type,
        "X-HedgeSpark-Event-Id": d.event_id,
        "X-HedgeSpark-Signature": sig_header,
        "X-HedgeSpark-Timestamp": ts,
    }

    d.attempts = (d.attempts or 0) + 1
    d.last_attempted_at = _now()

    try:
        import httpx
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            r = client.post(sub.target_url, content=body, headers=headers)
        d.response_status = r.status_code
        d.response_body = (r.text or "")[:500]
        if 200 <= r.status_code < 300:
            d.status = "delivered"
            d.delivered_at = _now()
            sub.last_success_at = _now()
            sub.consecutive_failures = 0
            db.flush()
            return "delivered"
        else:
            _record_failure(db, d, sub)
            return d.status
    except Exception as exc:
        d.response_status = 0
        d.response_body = f"transport_error: {type(exc).__name__}: {str(exc)[:300]}"
        _record_failure(db, d, sub)
        return d.status


def _record_failure(
    db: Session,
    d: OutboundWebhookDelivery,
    sub: OutboundWebhookSubscription,
) -> None:
    sub.last_failure_at = _now()
    sub.consecutive_failures = (sub.consecutive_failures or 0) + 1
    if d.attempts >= _MAX_ATTEMPTS:
        d.status = "dead"
    else:
        d.status = "pending"  # eligible for retry
    if sub.consecutive_failures >= _AUTO_DISABLE_AFTER and not sub.auto_disabled:
        sub.status = "disabled"
        sub.auto_disabled = True
    db.flush()


def deliver_pending_batch(db: Session, *, limit: int = 50) -> dict:
    """
    Worker entry point — sweep `pending` deliveries whose backoff has
    elapsed, attempt each. Caller schedules cron / loop.
    """
    now = _now()
    rows = (
        db.query(OutboundWebhookDelivery)
        .filter(OutboundWebhookDelivery.status == "pending")
        .order_by(OutboundWebhookDelivery.id.asc())
        .limit(limit)
        .all()
    )
    delivered = failed = skipped = 0
    for d in rows:
        idx = min(max(d.attempts - 1, 0), len(_BACKOFF_SECONDS) - 1)
        next_eligible = (d.last_attempted_at or d.created_at) + timedelta(seconds=_BACKOFF_SECONDS[idx])
        if next_eligible > now:
            skipped += 1
            continue
        result = attempt_delivery(db, d.id)
        if result == "delivered":
            delivered += 1
        else:
            failed += 1
    return {"delivered": delivered, "failed": failed, "skipped": skipped, "scanned": len(rows)}
