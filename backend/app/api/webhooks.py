"""
webhooks.py — Shopify webhook ingestion endpoints.

POST /webhooks/shopify/orders-paid
    Receives Shopify's orders/paid webhook.
    Parses the order payload, validates structure, and upserts to shop_orders.

    Authentication
    --------------
    No HMAC verification in v1 — this is intentional and documented below.
    Full Shopify HMAC verification (X-Shopify-Hmac-Sha256 header) must be
    added before exposing this endpoint to the public internet.  For the
    current deployment behind a private/known IP, this is an acceptable
    temporary posture.

    TODO: Add HMAC verification using SHOPIFY_WEBHOOK_SECRET before going live.
    See: https://shopify.dev/docs/api/admin-rest/current/webhook-validation

    Shop domain
    -----------
    Taken from the X-Shopify-Shop-Domain header that Shopify sends with every
    webhook.  If the header is absent (manual test call), falls back to the
    ?shop= query param.  Returns 400 if neither is present.

    Idempotency
    -----------
    Shopify guarantees at-least-once delivery.  The upsert_order service
    handles duplicates silently — duplicate deliveries return 200 with
    "stored": false rather than an error.

    Response codes
    --------------
    200 — order stored or duplicate silently skipped
    400 — missing shop domain OR payload structurally invalid (missing id or
          total_price)
    500 — unexpected server error (logged; should never happen in practice
          because the service layer catches all exceptions)

    Shopify retries on any non-2xx response, so 400 is intentionally returned
    only for permanently invalid payloads that will never succeed on retry.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.order_ingestion import parse_shopify_order, upsert_order
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# ---------------------------------------------------------------------------
# HMAC verification
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET: str = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")

# When ALLOW_INSECURE_DEV=true the HMAC check is bypassed if the secret is
# absent — acceptable only in a private development environment.  Production
# deployments must NEVER set this flag.
_ALLOW_INSECURE_DEV: bool = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"


def _verify_shopify_hmac(body: bytes, shopify_hmac: str | None) -> bool:
    """
    Verify the X-Shopify-Hmac-Sha256 header against the raw request body.

    Returns True when:
      a. SHOPIFY_WEBHOOK_SECRET is configured AND the HMAC matches, OR
      b. SHOPIFY_WEBHOOK_SECRET is NOT configured AND ALLOW_INSECURE_DEV=true
         (explicit dev-mode bypass — operator opted in).

    Returns False when:
      - Secret is missing and ALLOW_INSECURE_DEV is not set (safe default:
        fail closed — all webhook requests are rejected until the secret is
        configured).
      - Secret is configured but HMAC header is missing or incorrect.

    This design fails closed by default: an unconfigured production deployment
    rejects all webhook requests rather than silently accepting forged payloads.
    """
    if not _WEBHOOK_SECRET:
        if _ALLOW_INSECURE_DEV:
            log.warning(
                "webhooks: SHOPIFY_WEBHOOK_SECRET not configured — "
                "HMAC verification DISABLED (ALLOW_INSECURE_DEV=true). "
                "This must never be used in production."
            )
            return True  # explicit dev-mode bypass — operator opted in
        log.error(
            "webhooks: SHOPIFY_WEBHOOK_SECRET is not set and ALLOW_INSECURE_DEV is "
            "not enabled — rejecting all webhook requests. "
            "Set SHOPIFY_WEBHOOK_SECRET in backend/.env and reload PM2."
        )
        return False  # fail closed — do not process unauthenticated webhooks

    if not shopify_hmac:
        log.warning("webhooks: missing X-Shopify-Hmac-Sha256 header — rejected")
        return False

    try:
        expected = base64.b64encode(
            hmac.new(_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")
    except Exception as exc:
        log.error("webhooks: HMAC computation failed: %s", exc)
        return False

    if not hmac.compare_digest(expected, shopify_hmac):
        log.warning("webhooks: HMAC mismatch — payload rejected (possible forgery)")
        return False

    return True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# POST /webhooks/shopify/orders-paid
# ---------------------------------------------------------------------------

@router.post("/shopify/orders-paid")
async def shopify_orders_paid(
    request: Request,
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop: str | None = Query(default=None),
):
    """
    Ingest a Shopify orders/paid webhook.

    Shopify sends this event when an order is marked as paid.  We extract
    the revenue data, parse it, and upsert to shop_orders for use by:

      - Per-merchant AOV computation (replaces DEFAULT_AOV = 50.0)
      - Real conversion rate tracking (replaces infer_conversion_outcome)
      - Revenue attribution per product (via line_items.product_id)
      - Impact measurement for completed action tasks (feedback loop v1)

    Returns {"stored": true} on insert, {"stored": false} on duplicate.

    Security: Shopify HMAC signature verified when SHOPIFY_WEBHOOK_SECRET is set.
    Set SHOPIFY_WEBHOOK_SECRET in .env before exposing this endpoint publicly.
    """
    # Read raw body first — needed for HMAC verification before JSON parsing
    raw_body = await request.body()

    # Resolve shop domain: header takes priority over query param
    shop_domain = x_shopify_shop_domain or shop
    if not shop_domain:
        log.warning("webhooks/orders-paid: missing shop domain (no header or ?shop=)")
        raise HTTPException(
            status_code=400,
            detail="Missing shop domain. Include X-Shopify-Shop-Domain header or ?shop= param.",
        )

    # Validate shop domain format
    if not is_valid_shop_domain(shop_domain):
        log.warning("webhooks/orders-paid: invalid shop_domain=%r — rejected", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid shop domain format.")

    # Verify Shopify HMAC signature — guards against forged order injections
    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    # Parse JSON body
    try:
        payload: dict = json.loads(raw_body)
    except Exception:
        log.warning("webhooks/orders-paid: could not parse JSON body for shop=%s", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    log.info(
        "webhooks/orders-paid: received order id=%s shop=%s",
        payload.get("id"), shop_domain,
    )

    # Parse and validate
    order_data = parse_shopify_order(payload=payload, shop_domain=shop_domain)
    if order_data is None:
        log.warning(
            "webhooks/orders-paid: rejected invalid payload for shop=%s (missing id or total_price)",
            shop_domain,
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid order payload: 'id' and 'total_price' are required.",
        )

    # Upsert — open a fresh DB session scoped to this request
    db: Session = SessionLocal()
    try:
        order, created = upsert_order(db=db, order_data=order_data)
        return {
            "stored":           created,
            "shopify_order_id": order.shopify_order_id,
            "shop_domain":      order.shop_domain,
            "total_price":      order.total_price,
            "currency":         order.currency,
        }
    except Exception as exc:
        log.exception(
            "webhooks/orders-paid: unexpected error for shop=%s order_id=%s: %s",
            shop_domain, order_data.get("shopify_order_id"), exc,
        )
        raise HTTPException(status_code=500, detail="Internal error persisting order.")
    finally:
        db.close()
