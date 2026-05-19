"""
public_events.py — HMAC-signed inbound event API (ζ3).

Merchants push custom events from their own stack (Klaviyo open,
Gorgias reply, Stripe chargeback, Shopify Flow trigger). Events feed
the internal event_bus AND can evaluate merchant_rules.

Authentication
--------------
Per-merchant API key stored encrypted in `merchants.public_api_key_hash`.
Signature: HMAC-SHA256(body, secret) in header `X-HS-Signature`.
Replay-proof: `X-HS-Timestamp` must be within 5 minutes of server clock.

Endpoint
--------
POST /pub/events
    Headers:
      X-HS-Shop-Domain: merchant.myshopify.com
      X-HS-Signature: hmac-sha256=...
      X-HS-Timestamp: 1723456789
    Body:
      {"event_name": "klaviyo_email_opened",
       "email": "x@y.com",
       "properties": {...}}

Rate-limited: 120 events/min/shop.
Idempotent: POST with same event_id skipped.

Writes to:
  - event_bus (analytics_events)
  - rule_engine.evaluate_trigger (fires any active merchant_rules)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text as sql_text

from app.core.database import SessionLocal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pub", tags=["public_events"])

_CLOCK_SKEW_SECONDS = 300   # 5 min — replay window
_RATE_LIMIT_PER_MIN = 120   # per shop
_DEDUP_TTL_S = 3600         # 1h

# In-process sliding-window counter — used as fail-CLOSED fallback when
# Redis is unavailable. Pre-2026-05-08 the rate-limit returned True on
# Redis hiccup ("fail-open"), which combined with the per-shop secret
# fallback (#5) allowed unlimited event flooding during a Redis blip.
# Deque per shop, bounded by _RATE_LIMIT_PER_MIN; the deque length never
# exceeds the cap so memory is O(shops × cap) which is bounded.
from collections import defaultdict as _defaultdict, deque as _deque
import threading as _threading
_LOCAL_RATE_BUCKETS: dict[str, "_deque[float]"] = _defaultdict(_deque)  # F821: forward-ref must match the in-scope alias (`_deque`, not `deque`)
_LOCAL_RATE_LOCK = _threading.Lock()


ALLOWED_PUBLIC_EVENTS = frozenset({
    "klaviyo_email_opened",
    "klaviyo_email_clicked",
    "klaviyo_email_converted",
    "gorgias_ticket_opened",
    "gorgias_ticket_resolved",
    "stripe_chargeback",
    "stripe_refund_requested",
    "shopify_flow_trigger",
    "custom_signal",
})


class PublicEventPayload(BaseModel):
    event_name: str = Field(..., min_length=2, max_length=64)
    event_id: str | None = Field(None, max_length=128)
    email: str | None = Field(None, max_length=256)
    visitor_id: str | None = Field(None, max_length=128)
    product_url: str | None = Field(None, max_length=512)
    revenue_eur: float | None = None
    properties: dict | None = None

    @field_validator("properties")
    @classmethod
    def _check_properties_size(cls, v):
        # Defense vs payload amplification: even with HMAC gating, a
        # compromised shop secret should not allow a single POST to
        # generate a 100MB row in `events_metadata` or burn worker
        # memory. 8KB ceiling covers legitimate properties (cart line
        # items, custom flow context) without enabling abuse.
        if v is None:
            return v
        try:
            import json as _json
            raw = _json.dumps(v, default=str)
        except Exception:
            return None
        if len(raw) > 8 * 1024:
            return None
        return v


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Compare HMAC-SHA256 — constant-time."""
    expected = "hmac-sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature.strip(), expected)


def _lookup_secret(db, shop_domain: str) -> str | None:
    """Fetch the public API secret for a shop.

    Schema: `merchants.public_api_key_hash` per row.

    Pre-2026-05-08 this fell back to a shared `PUBLIC_EVENTS_DEV_SECRET`
    env var when the per-shop column was empty — meaning ONE leaked dev
    secret would forge events for EVERY shop without a configured key.
    The fallback is removed: shops without a configured secret are
    rejected at signature verification (caller path returns 401).
    """
    try:
        row = db.execute(
            sql_text(
                "SELECT public_api_key_hash FROM merchants WHERE shop_domain = :s LIMIT 1"
            ),
            {"s": shop_domain},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception as exc:
        log.warning("public_events: secret lookup failed: %s", exc)  # column may not exist yet
    return None


def _local_rate_allow(shop_domain: str) -> bool:
    """In-process sliding-window check — used when Redis is unavailable.
    Each shop has a bounded deque of timestamps within the last 60s.
    """
    now = time.monotonic()
    cutoff = now - 60.0
    with _LOCAL_RATE_LOCK:
        bucket = _LOCAL_RATE_BUCKETS[shop_domain]
        # evict expired
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_PER_MIN:
            return False
        bucket.append(now)
        return True


def _rate_allow(shop_domain: str) -> bool:
    """Rate-limit check. Tries Redis first; falls back to in-process
    sliding-window when Redis is unavailable. Pre-2026-05-08 this
    returned True on any exception (fail-open), allowing unlimited
    event flooding during Redis hiccups. Now fail-CLOSED-with-fallback.
    """
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("public_events.rate_limit.local_fallback")
            return _local_rate_allow(shop_domain)
        key = f"hs:pub_events:rate:{shop_domain}:{int(time.time() // 60)}"
        count = rc.incr(key)
        rc.expire(key, 75)
        return int(count) <= _RATE_LIMIT_PER_MIN
    except Exception as exc:
        # Fail-CLOSED-with-fallback: Redis hiccup → check in-process
        # sliding window instead of unconditionally allowing. Bounds the
        # blast radius of a Redis outage to per-shop cap × #workers.
        log.warning(
            "public_events: rate limit redis check failed, using local "
            "in-process fallback: %s", exc
        )
        return _local_rate_allow(shop_domain)


def _is_duplicate(shop_domain: str, event_id: str) -> bool:
    if not event_id:
        return False
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("public_events.dedup")
            return False
        key = f"hs:pub_events:dedup:{shop_domain}:{event_id}"
        # setnx: return False if we set (not duplicate), True if key existed
        if rc.exists(key):
            return True
        rc.setex(key, _DEDUP_TTL_S, "1")
        return False
    except Exception as exc:
        log.warning("public_events: dedup check failed: %s", exc)
        return False


@router.post("/events")
async def ingest_public_event(
    request: Request,
    payload: PublicEventPayload,
    x_hs_shop_domain: str = Header(..., convert_underscores=False, alias="X-HS-Shop-Domain"),
    x_hs_signature: str = Header(..., convert_underscores=False, alias="X-HS-Signature"),
    x_hs_timestamp: str = Header(..., convert_underscores=False, alias="X-HS-Timestamp"),
):
    # Replay protection
    try:
        ts = int(x_hs_timestamp)
    except ValueError:
        raise HTTPException(401, "invalid_timestamp")
    now = int(time.time())
    if abs(now - ts) > _CLOCK_SKEW_SECONDS:
        raise HTTPException(401, "timestamp_out_of_range")

    # Rate limit
    if not _rate_allow(x_hs_shop_domain):
        raise HTTPException(429, "rate_limited")

    # Allowlist
    if payload.event_name not in ALLOWED_PUBLIC_EVENTS:
        raise HTTPException(422, f"event_not_allowed:{payload.event_name}")

    # Verify signature
    db = SessionLocal()
    try:
        secret = _lookup_secret(db, x_hs_shop_domain)
        if not secret:
            raise HTTPException(401, "no_secret_configured")

        raw_body = await request.body()
        if not _verify_signature(raw_body, x_hs_signature, secret):
            raise HTTPException(401, "invalid_signature")

        # Dedup
        if _is_duplicate(x_hs_shop_domain, payload.event_id or ""):
            return {"accepted": True, "deduped": True, "event_id": payload.event_id}

        # Forward to event_bus
        from app.services.event_bus import emit as bus_emit
        # Map to allowed event_bus names (fall back to custom_signal)
        bus_event = "session_start"  # default inert mapping
        if payload.event_name in ("klaviyo_email_opened", "klaviyo_email_clicked", "klaviyo_email_converted"):
            bus_event = "signal_detected"
        elif payload.event_name in ("stripe_chargeback", "stripe_refund_requested"):
            bus_event = "checkout_started"  # best-fit, not perfect
        elif payload.event_name == "shopify_flow_trigger":
            bus_event = "signal_detected"

        bus_emit(
            bus_event,
            shop_domain=x_hs_shop_domain,
            visitor_id=payload.visitor_id,
            product_url=payload.product_url,
            revenue_eur=payload.revenue_eur,
            props={
                "source_event": payload.event_name,
                "email_hash": (
                    hashlib.sha256(payload.email.encode()).hexdigest()[:16]
                    if payload.email else None
                ),
                **(payload.properties or {}),
            },
            db=db,
        )

        # Evaluate merchant rules
        try:
            from app.services.rule_engine import evaluate_trigger
            fired = evaluate_trigger(
                db,
                shop_domain=x_hs_shop_domain,
                trigger_signal=payload.event_name,
                payload={
                    "email": payload.email,
                    "visitor_id": payload.visitor_id,
                    "product_url": payload.product_url,
                    "revenue_eur": payload.revenue_eur,
                    **(payload.properties or {}),
                },
            )
        except Exception as exc:
            log.debug("public_events: rule eval failed: %s", exc)
            fired = 0

        db.commit()
        return {
            "accepted": True,
            "event_id": payload.event_id,
            "rules_fired": fired,
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("public_events: ingestion failed: %s", exc)
        try:
            db.rollback()
        except Exception as exc:
            log.warning("public_events: rollback failed: %s", exc)
        raise HTTPException(500, "internal_error")
    finally:
        db.close()
