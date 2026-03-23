"""
webhooks.py — Shopify webhook ingestion endpoints.

All endpoints share a single HMAC verification function that fails closed:
requests are rejected when the webhook secret is missing or the signature
is invalid.  The raw request body is always read before JSON parsing so
the bytes used for HMAC computation exactly match what Shopify signed.

Endpoints
---------
POST /webhooks/shopify/orders-paid
    Ingests orders/paid.  Stores revenue data in shop_orders.
    Used by: AOV computation, attribution, calibration.

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
    orders-paid: upsert_order handles duplicate order IDs silently.
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
# POST /webhooks/shopify/orders-paid
# ---------------------------------------------------------------------------

@router.post("/shopify/orders-paid")
async def shopify_orders_paid(
    request:               Request,
    db:                    Session = Depends(get_db),
    x_shopify_shop_domain: str | None = Header(default=None),
    x_shopify_hmac_sha256: str | None = Header(default=None),
    shop:                  str | None = Query(default=None),
):
    """
    Ingest an orders/paid webhook from Shopify.

    Stores revenue data in shop_orders for AOV, attribution, and calibration.

    Security: HMAC verified against SHOPIFY_WEBHOOK_SECRET before any
    payload processing.  The raw body is read first to preserve the bytes
    exactly as Shopify signed them.
    """
    # Raw body first — HMAC must be verified before parsing
    raw_body = await request.body()

    # Resolve shop domain
    shop_domain = x_shopify_shop_domain or shop
    if not shop_domain:
        log.warning("webhooks/orders-paid: missing shop domain")
        raise HTTPException(
            status_code=400,
            detail="Missing shop domain. Include X-Shopify-Shop-Domain header or ?shop= param.",
        )

    if not is_valid_shop_domain(shop_domain):
        log.warning("webhooks/orders-paid: invalid shop_domain=%r", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid shop domain format.")

    # HMAC verification — fail closed
    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    # Parse JSON
    try:
        payload: dict = json.loads(raw_body)
    except Exception:
        log.warning("webhooks/orders-paid: JSON parse failed for shop=%s", shop_domain)
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    log.info(
        "webhooks/orders-paid: received order id=%s shop=%s",
        payload.get("id"), shop_domain,
    )

    # Validate and parse order
    order_data = parse_shopify_order(payload=payload, shop_domain=shop_domain)
    if order_data is None:
        log.warning(
            "webhooks/orders-paid: invalid payload for shop=%s — missing id or total_price",
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
            "webhooks/orders-paid: unexpected error shop=%s order_id=%s: %s",
            shop_domain, order_data.get("shopify_order_id"), exc,
        )
        raise HTTPException(status_code=500, detail="Internal error persisting order.")


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
# v1 implementation: HMAC-verified log-and-acknowledge.
# Shopify accepts a 200 response and does not retry.
# Actual data deletion pipeline is a future task.
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
    customer's data (typically after customer submits a GDPR data deletion
    request to the merchant).

    v1: Verify HMAC, log for audit trail, return 200.
    TODO: Queue deletion of visitor_purchase_sessions, nudge_events, events
          rows attributable to this customer's visitor_id(s).
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    try:
        payload = json.loads(raw_body)
        customer_id = payload.get("customer", {}).get("id", "unknown")
        log.info(
            "webhooks/customers-redact: received shop=%s customer_id=%s — "
            "ACKNOWLEDGED (data deletion pipeline not yet implemented)",
            shop_domain, customer_id,
        )
    except Exception:
        log.info(
            "webhooks/customers-redact: received shop=%s — "
            "payload parse failed but ACKNOWLEDGED",
            shop_domain,
        )

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

    Shopify delivers this when a customer requests a copy of their data
    from the merchant.  The app must provide whatever data it holds.

    v1: Verify HMAC, log for audit trail, return 200.
    TODO: Build a data export pipeline that identifies and returns all
          event rows and session data attributable to the customer.
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    try:
        payload = json.loads(raw_body)
        customer_id = payload.get("customer", {}).get("id", "unknown")
        log.info(
            "webhooks/customers-data-request: received shop=%s customer_id=%s — "
            "ACKNOWLEDGED (data export pipeline not yet implemented)",
            shop_domain, customer_id,
        )
    except Exception:
        log.info(
            "webhooks/customers-data-request: received shop=%s — "
            "payload parse failed but ACKNOWLEDGED",
            shop_domain,
        )

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

    v1: Verify HMAC, log for audit trail, return 200.
    The app/uninstalled webhook (received 48h earlier) already nullified
    the access token.  This endpoint acknowledges the full data deletion
    request; the actual deletion pipeline is a future task.

    TODO: Queue full data deletion:
      DELETE FROM events WHERE shop_domain = ?
      DELETE FROM shop_orders WHERE shop_domain = ?
      DELETE FROM nudge_events WHERE shop_domain = ?
      DELETE FROM visitor_purchase_sessions WHERE shop_domain = ?
      DELETE FROM active_nudges WHERE shop_domain = ?
      DELETE FROM opportunity_signals WHERE shop_domain = ?
      DELETE FROM merchants WHERE shop_domain = ?  (final step)
    """
    raw_body = await request.body()

    if not _verify_shopify_hmac(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="HMAC verification failed.")

    shop_domain = x_shopify_shop_domain or "unknown"
    log.info(
        "webhooks/shop-redact: received for shop=%s — "
        "ACKNOWLEDGED (full data deletion pipeline not yet implemented). "
        "Access token was already nullified by app/uninstalled webhook.",
        shop_domain,
    )

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

    return {"status": "acknowledged"}
