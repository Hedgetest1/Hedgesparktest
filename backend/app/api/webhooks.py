"""
TIER_2 — modification requires explicit human approval (CLAUDE.md §10).

webhooks.py — Shopify webhook ingestion endpoints.

All endpoints share a single HMAC verification function that fails closed:
requests are rejected when the webhook secret is missing or the signature
is invalid.  The raw request body is always read before JSON parsing so
the bytes used for HMAC computation exactly match what Shopify signed.

Endpoints
---------
POST /webhooks/shopify/orders
    Ingests orders/updated.  Stores revenue data in shop_orders.
    Used by: AOV computation, attribution, calibration.
    Compat aliases: /orders-created, /orders-paid (kept until fully migrated).

POST /webhooks/shopify/app-uninstalled
    Shopify app/uninstalled lifecycle event.
    Nullifies the stored access token, marks merchant as uninstalled,
    disables billing.  Idempotent.

POST /webhooks/shopify/customers-redact      (GDPR mandatory)
POST /webhooks/shopify/customers-data-request (GDPR mandatory)
POST /webhooks/shopify/shop-redact           (GDPR mandatory)
    GDPR compliance endpoints required for Shopify App Store submission.
    v1: verifies HMAC, logs request for audit trail, returns 200.
    Data deletion is acknowledged and queued; full pipeline TBD.
    Shopify accepts 200 responses and does not retry if you return 200.

HMAC verification
-----------------
Every endpoint calls _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256).

Algorithm (Shopify-specified):
    expected = base64(HMAC-SHA256(SHOPIFY_WEBHOOK_SECRET, raw_body_bytes))
    compare with X-Shopify-Hmac-Sha256 header using timing-safe comparison

Fail-closed behaviour:
  - SHOPIFY_WEBHOOK_SECRET not set + ALLOW_INSECURE_DEV not set → 401 always
  - SHOPIFY_WEBHOOK_SECRET not set + ALLOW_INSECURE_DEV=true → bypass (dev only)
  - SHOPIFY_WEBHOOK_SECRET set + header missing → 401
  - SHOPIFY_WEBHOOK_SECRET set + signature mismatch → 401
  - SHOPIFY_WEBHOOK_SECRET set + signature valid → proceed

Body parsing rules:
    Raw body is captured with `await request.body()` before any JSON parsing.
    This guarantees the bytes fed to HMAC are identical to what Shopify signed.
    Parsing the body first (FastAPI Pydantic model parameter) would consume the
    stream and corrupt the HMAC computation — we always read raw first.

Idempotency:
    orders: upsert_order handles duplicate order IDs silently.
    app-uninstalled: re-processing an already-uninstalled shop is a no-op.
    GDPR endpoints: stateless log-and-ack, idempotent by nature.

Session management:
    All endpoints use Depends(get_db) — request-scoped sessions returned to
    the pool after the response is finalized.  No raw SessionLocal() calls.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.merchant import Merchant
from app.services.order_ingestion import parse_shopify_order, upsert_order
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# ---------------------------------------------------------------------------
# HMAC verification — shared by all Shopify webhook endpoints
# ---------------------------------------------------------------------------

_WEBHOOK_SECRET:     str  = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
_ALLOW_INSECURE_DEV: bool = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"


def _verify_shopify_hmac(body: bytes, shopify_hmac: str | None) -> bool:
    """
    Verify the X-Shopify-Hmac-Sha256 header against the raw request body bytes.

    The header value is base64-encoded HMAC-SHA256(secret, body).
    Uses hmac.compare_digest to prevent timing side-channels.

    Fail-closed contract:
      - No secret configured + no dev bypass → False (reject everything)
      - No secret + ALLOW_INSECURE_DEV=true → True with WARNING (dev only)
      - Secret set + header absent → False
      - Secret set + mismatch → False
      - Secret set + match → True

    This function must NEVER be called after body parsing — only before.
    The `body` parameter must be the raw bytes exactly as received from Shopify.
    """
    if not _WEBHOOK_SECRET:
        if _ALLOW_INSECURE_DEV:
            log.warning(
                "webhooks: HMAC verification DISABLED — "
                "SHOPIFY_WEBHOOK_SECRET not set, ALLOW_INSECURE_DEV=true. "
                "NEVER run this way in production."
            )
            return True
        log.error(
            "webhooks: SHOPIFY_WEBHOOK_SECRET not configured and dev bypass not active — "
            "all webhook requests will be rejected until the secret is set. "
            "Add SHOPIFY_WEBHOOK_SECRET to backend/.env and reload PM2."
        )
        return False

    if not shopify_hmac:
        log.warning("webhooks: X-Shopify-Hmac-Sha256 header missing — request rejected")
        return False

    try:
        expected = base64.b64encode(
            hmac.new(_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
        ).decode("utf-8")
    except Exception as exc:
        log.error("webhooks: HMAC computation error: %s", exc)
        return False

    if not hmac.compare_digest(expected, shopify_hmac):
        log.warning("webhooks: HMAC mismatch — request rejected (possible forgery or replay)")
        return False

    return True


# ---------------------------------------------------------------------------
# Shared utility
# ---------------------------------------------------------------------------

def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# POST /webhooks/shopify/orders
# MVP: uses orders/updated topic — not protected by Shopify customer data rules
# Compat aliases kept for /orders-created and /orders-paid until fully migrated
# ---------------------------------------------------------------------------

async def _ingest_order(
    request:               Request,
    db:                    Session,
    x_shopify_shop_domain: str | None,
    x_shopify_hmac_sha256: str | None,
    shop:                  str | None,
):
    """
    Shared handler for Shopify order webhooks (orders/updated).

    Stores revenue data in shop_orders for AOV, attribution, and calibration.
    Idempotent: duplicate shopify_order_id deliveries are upserted silently.

    Security: HMAC verified against SHOPIFY_WEBHOOK_SECRET before any
    payload processing.  The raw body is read first to preserve the bytes
    exactly as Shopify signed them.
    """
    # Raw body first — HMAC must be verified before parsing
    raw_body = await request.body()

    # Resolve shop domain
    shop_domain = x_shopify_shop_domain or shop
    if not shop_domain:
        log.warning("webhooks/orders: missing shop domain")
        raise HTTPException(
            status_code=400,
            detail="Missing shop domain. Include X-Shopify-Shop-Domain header or ?shop= param.",
        )

    if not is_valid_shop_domain(shop_domain):
        log.warning("webhooks/orders: invalid shop_domain=%r", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid shop domain format.")

    # HMAC verification — fail closed
    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    # Parse JSON
    try:
        payload: dict = json.loads(raw_body)
    except Exception as exc:
        log.warning("webhooks/orders: JSON parse failed for shop=%s", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    log.info(
        "webhooks/orders: received order id=%s shop=%s",
        payload.get("id"), shop_domain,
    )

    # Validate and parse order
    order_data = parse_shopify_order(payload=payload, shop_domain=shop_domain)
    if order_data is None:
        log.warning(
            "webhooks/orders: invalid payload for shop=%s — missing id or total_price",
            shop_domain,
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid order payload: 'id' and 'total_price' are required.",
        )

    # Upsert
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
            "webhooks/orders: unexpected error shop=%s order_id=%s: %s",
            shop_domain, order_data.get("shopify_order_id"), exc,
        )
        raise HTTPException(status_code=500, detail="Internal error persisting order.")


@router.post("/shopify/orders")
async def shopify_orders(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop:                  str | None = Query(default=None),
):
    """Ingest an orders/updated webhook from Shopify (primary endpoint)."""
    return await _ingest_order(request, db, x_shopify_shop_domain, x_shopify_hmac_sha256, shop)


@router.post("/shopify/orders-created")
async def shopify_orders_created_compat(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop:                  str | None = Query(default=None),
):
    """Backward-compat alias for orders-created route."""
    return await _ingest_order(request, db, x_shopify_shop_domain, x_shopify_hmac_sha256, shop)


@router.post("/shopify/orders-paid")
async def shopify_orders_paid_compat(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop:                  str | None = Query(default=None),
):
    """Backward-compat alias for orders-paid route."""
    return await _ingest_order(request, db, x_shopify_shop_domain, x_shopify_hmac_sha256, shop)


# ---------------------------------------------------------------------------
# POST /webhooks/shopify/app-uninstalled
# ---------------------------------------------------------------------------

@router.post("/shopify/app-uninstalled")
async def shopify_app_uninstalled(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop:                  str | None = Query(default=None),
):
    """
    Handle Shopify app/uninstalled lifecycle webhook.

    When a merchant removes the app from their Shopify admin, Shopify
    delivers this webhook.  We must:

    1. Verify HMAC (same secret as all webhooks).
    2. Nullify the stored access token — stale admin-level credentials
       must not remain in the database after uninstall.
    3. Mark merchant as uninstalled (install_status = "uninstalled").
    4. Record uninstalled_at timestamp for audit trail.
    5. Set billing_active = False — any active billing is cancelled on
       uninstall from the Shopify side; we mirror that state.

    Idempotency:
        If the merchant reinstalls later, the OAuth callback will overwrite
        these fields (access_token, install_status → "active", etc.).
        If this webhook is delivered twice, the second pass is a no-op
        (same fields written to the same values).

    What is intentionally NOT deleted:
        - The merchant row itself — preserved for audit trail and historical
          analytics.  shop_domain + install history is not PII.
        - shop_orders, events, nudge_events — historical data retained.
          The shop/redact GDPR webhook handles deletion when required.
        - billing_charge_id — retained for billing audit trail.
    """
    raw_body = await request.body()

    shop_domain = x_shopify_shop_domain or shop
    if not shop_domain:
        log.warning("webhooks/app-uninstalled: missing shop domain — rejected")
        raise HTTPException(status_code=400, detail="Missing shop domain.")

    if not is_valid_shop_domain(shop_domain):
        log.warning("webhooks/app-uninstalled: invalid shop_domain=%r — rejected", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid shop domain format.")

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    log.info("webhooks/app-uninstalled: processing for shop=%s", shop_domain)

    # Fetch merchant row
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()

    if merchant is None:
        # Unknown shop — no row to clean up.  Return 200 so Shopify doesn't retry.
        log.warning(
            "webhooks/app-uninstalled: no merchant row found for shop=%s — "
            "returning 200 to prevent Shopify retry",
            shop_domain,
        )
        return {"status": "ok", "shop": shop_domain}

    if merchant.install_status == "uninstalled":
        # Already processed — idempotent no-op
        log.info(
            "webhooks/app-uninstalled: shop=%s already marked uninstalled — no-op",
            shop_domain,
        )
        return {"status": "ok", "shop": shop_domain}

    # Execute cleanup
    now = _now_naive()
    merchant.access_token    = None          # nullify credentials immediately
    merchant.install_status  = "uninstalled"
    merchant.uninstalled_at  = now
    merchant.billing_active  = False
    # Do NOT reset plan — preserve for audit trail and for reinstall detection
    # Do NOT clear webhook_id / script_tag_id — retain for audit trail

    try:
        db.commit()
        log.info(
            "webhooks/app-uninstalled: cleaned up shop=%s — "
            "token nullified, billing disabled, status=uninstalled",
            shop_domain,
        )
    except Exception as exc:
        log.error(
            "webhooks/app-uninstalled: DB commit failed for shop=%s: %s",
            shop_domain, exc,
        )
        db.rollback()
        # Still return 200 — Shopify retries on non-2xx responses.
        # Returning 200 prevents an infinite retry loop for a transient DB error.
        # The cleanup will be retried on the next Shopify delivery within 48h,
        # or can be triggered manually.
        return {"status": "error_logged", "shop": shop_domain}

    return {"status": "ok", "shop": shop_domain}


# ---------------------------------------------------------------------------
# GDPR mandatory endpoints (Shopify App Store requirement)
# ---------------------------------------------------------------------------
#
# Shopify requires these three webhook endpoints for any app in the App Store.
# They are registered in the Shopify Partner Dashboard under GDPR webhooks.
#
# v2 implementation: HMAC-verified, creates a GdprRequest row for the
# gdpr_worker to process asynchronously.  Shopify accepts 200 and does
# not retry.  Actual deletion happens in the background worker.
# ---------------------------------------------------------------------------

@router.post("/shopify/customers-redact")
async def shopify_customers_redact(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    """
    GDPR customers/redact webhook.

    Shopify delivers this when a merchant requests deletion of a specific
    customer's data.  Creates a GdprRequest row for async processing by
    the gdpr_worker.
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    customer_id = None
    customer_email = None
    payload_str = raw_body.decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw_body)
        customer = payload.get("customer", {})
        customer_id = str(customer.get("id", "")) or None
        customer_email = customer.get("email") or None
    except Exception as exc:
        log.warning("webhooks: shopify_customers_redact failed: %s", exc)

    from app.models.gdpr_request import GdprRequest
    gdpr_req = GdprRequest(
        request_type="customers_redact",
        shop_domain=shop_domain,
        customer_id=customer_id,
        customer_email=customer_email,
        payload=payload_str,
    )
    try:
        db.add(gdpr_req)
        db.commit()
        db.refresh(gdpr_req)
        log.info(
            "webhooks/customers-redact: queued gdpr_request_id=%d shop=%s customer_id=%s",
            gdpr_req.id, shop_domain, customer_id,
        )
        return {"status": "queued", "gdpr_request_id": gdpr_req.id}
    except Exception as exc:
        db.rollback()
        log.error("webhooks/customers-redact: failed to queue — %s", exc)
        return {"status": "acknowledged"}


@router.post("/shopify/customers-data-request")
async def shopify_customers_data_request(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    """
    GDPR customers/data_request webhook.

    Shopify delivers this when a customer requests a copy of their data.
    Creates a GdprRequest row.  v1: marked completed with acknowledgement.
    Full export pipeline is a future enhancement.
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    customer_id = None
    payload_str = raw_body.decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw_body)
        customer_id = str(payload.get("customer", {}).get("id", "")) or None
    except Exception as exc:
        log.warning("webhooks: shopify_customers_data_request failed: %s", exc)

    from app.models.gdpr_request import GdprRequest
    gdpr_req = GdprRequest(
        request_type="customers_data_request",
        shop_domain=shop_domain,
        customer_id=customer_id,
        payload=payload_str,
    )
    try:
        db.add(gdpr_req)
        db.commit()
        db.refresh(gdpr_req)
        log.info(
            "webhooks/customers-data-request: queued gdpr_request_id=%d shop=%s customer_id=%s",
            gdpr_req.id, shop_domain, customer_id,
        )
        return {"status": "queued", "gdpr_request_id": gdpr_req.id}
    except Exception as exc:
        db.rollback()
        log.error("webhooks/customers-data-request: failed to queue — %s", exc)
        return {"status": "acknowledged"}


@router.post("/shopify/shop-redact")
async def shopify_shop_redact(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
):
    """
    GDPR shop/redact webhook.

    Shopify delivers this 48 hours after a merchant uninstalls the app,
    requesting that all of the shop's data be deleted.

    Creates a GdprRequest row for the gdpr_worker to process.
    Also ensures merchant is marked uninstalled immediately (defensive).
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    payload_str = raw_body.decode("utf-8", errors="replace")

    # Ensure merchant is marked uninstalled if app-uninstalled webhook was missed
    if shop_domain != "unknown" and is_valid_shop_domain(shop_domain):
        merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if merchant and merchant.install_status == "active":
            now = _now_naive()
            merchant.access_token   = None
            merchant.install_status = "uninstalled"
            merchant.uninstalled_at = merchant.uninstalled_at or now
            merchant.billing_active = False
            try:
                db.commit()
                log.info(
                    "webhooks/shop-redact: merchant row cleaned up for shop=%s "
                    "(app-uninstalled was not previously processed)",
                    shop_domain,
                )
            except Exception as exc:
                log.error(
                    "webhooks/shop-redact: cleanup commit failed shop=%s: %s",
                    shop_domain, exc,
                )
                db.rollback()

    # Queue full data deletion
    from app.models.gdpr_request import GdprRequest
    gdpr_req = GdprRequest(
        request_type="shop_redact",
        shop_domain=shop_domain,
        payload=payload_str,
    )
    try:
        db.add(gdpr_req)
        db.commit()
        db.refresh(gdpr_req)
        log.info(
            "webhooks/shop-redact: queued gdpr_request_id=%d shop=%s for full data deletion",
            gdpr_req.id, shop_domain,
        )
        return {"status": "queued", "gdpr_request_id": gdpr_req.id}
    except Exception as exc:
        db.rollback()
        log.error("webhooks/shop-redact: failed to queue — %s", exc)
        return {"status": "acknowledged"}
