from __future__ import annotations
"""
setup.py — Merchant setup status and repair endpoints.

Provides the backend surface for the onboarding / setup-status UX and for
operator-triggered repair of broken webhook / script-tag registrations.

Endpoints
---------
GET /setup/status?shop=<domain>[&deep=true]
    Returns the current setup/readiness state of the merchant's installation.

    ?deep=false (default)
        Fast DB-only audit.  Returns in < 5ms.  webhook_ok / tracker_ok
        are inferred from stored IDs.  Suitable for dashboard page load.

    ?deep=true
        Calls Shopify API to verify webhook and script_tag are actually
        registered.  Returns in ~1-2s (two Shopify REST calls).  Heals
        stale DB state as a side effect.

    Response shape:
    {
        "shop_domain":    "example.myshopify.com",
        "computed_at":    "2026-03-23T12:00:00+00:00",
        "audit_mode":     "fast" | "deep",
        "setup_complete": true | false,
        "readiness":      "degraded" | "needs_repair" | "lite_ready" | "pro_active",
        "degraded_reasons": [],
        "checks": {
            "merchant_exists":        true,
            "install_active":         true,
            "token_ok":               true,
            "token_encrypted":        true,
            "webhook_ok":             true,
            "webhook_id":             "12345678",
            "tracker_ok":             true,
            "tracker_id":             "87654321",
            "billing_active":         false,
            "billing_plan":           "lite",
            "billing_charge_pending": false
        }
    }

POST /setup/repair/webhook?shop=<domain>
    Idempotently re-registers the orders/updated webhook with Shopify.
    Updates merchant.webhook_id on success.
    Returns:
    {
        "repaired": true | false,
        "webhook_id": "...",
        "already_ok": true | false,
        "detail": "..."
    }

POST /setup/repair/tracker?shop=<domain>
    Idempotently re-injects spark-tracker.js as a Shopify Script Tag.
    Updates merchant.script_tag_id on success.
    Returns:
    {
        "repaired": true | false,
        "tracker_id": "...",
        "already_ok": true | false,
        "detail": "..."
    }

Auth
----
All endpoints require X-API-Key (DASHBOARD_API_KEY).
These are internal dashboard/operator endpoints — not exposed to storefronts.

Security model for repair endpoints
------------------------------------
Repair calls the Shopify Admin API using the merchant's stored access_token.
If the token is missing or decryption fails, repair returns 409 with a clear
message directing the merchant to reinstall.  No repair is possible without
a valid access token — the token is the credential.

Idempotency guarantee
---------------------
Both repair endpoints call ensure_orders_webhook / ensure_tracker_script_tag,
which list existing Shopify resources before creating.  Calling repair twice
will not create duplicate webhooks or script tags.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant
from app.services.setup_audit import compute_audit_deep, compute_audit_fast
from app.services.shopify_admin import ensure_orders_webhook, ensure_tracker_script_tag

log = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

_APP_URL = os.getenv("APP_URL", "").rstrip("/")


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tracker_url() -> str:
    from app.core.tracker_version import get_tracker_url
    return get_tracker_url()


# ---------------------------------------------------------------------------
# GET /setup/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def get_setup_status(
    shop: str = Depends(require_merchant_session),
    deep: bool = Query(default=False, description="When true, calls Shopify API to verify live state"),
    db:   Session = Depends(get_db),
):
    """
    Return the setup/readiness state for the given shop.

    deep=false (default): fast DB-only audit
    deep=true:            live Shopify API verification (slower, more accurate)
    """
    if deep:
        audit = await compute_audit_deep(db, shop)
    else:
        audit = compute_audit_fast(db, shop)

    return audit.to_dict()


# ---------------------------------------------------------------------------
# GET /setup/attribution-snippet — merchant-facing install snippet
# ---------------------------------------------------------------------------

@router.get("/attribution-snippet")
def get_attribution_snippet(
    shop: str = Depends(require_merchant_session),
):
    """
    Return the exact script tag the merchant must paste into
    Settings → Checkout → Order status page → Additional scripts.

    This enables visitor-to-order attribution linking so HedgeSpark
    can track which browsing behavior led to each purchase.
    """
    import os
    app_url = os.getenv("APP_URL", "")
    if not app_url:
        return {"snippet": None, "error": "Server APP_URL not configured"}

    snippet = (
        f'<script src="{app_url}/attribution.js'
        f'?shop={{{{ shop.permanent_domain }}}}" async></script>'
    )

    return {
        "snippet": snippet,
        "instructions": [
            "Go to Shopify Admin → Settings → Checkout",
            "Scroll to 'Order status page' → 'Additional scripts'",
            "Paste the snippet below into the text box",
            "Click Save",
        ],
        "why": (
            "This connects browsing behavior to purchases, enabling "
            "attribution intelligence, behavioral cohort analysis, and "
            "customer lifetime value tracking."
        ),
    }


# ---------------------------------------------------------------------------
# GET /setup/pixel-status — check if Custom Pixel is active
# ---------------------------------------------------------------------------

@router.get("/pixel-status")
def get_pixel_status(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """
    Check whether the Shopify Custom Pixel is sending purchase events.

    Returns pixel_active=true if at least one purchase event has been
    received from the pixel in the last 30 days. Also returns the pixel
    code snippet the merchant needs to install.
    """
    from sqlalchemy import text
    import os

    # Check for any purchase events from this shop
    row = db.execute(text(
        "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :shop AND source = 'pixel'"
    ), {"shop": shop}).fetchone()
    pixel_active = (row[0] or 0) > 0

    # Also check for recent events (in case orders haven't happened yet but events flow)
    event_row = db.execute(text(
        "SELECT COUNT(*) FROM events WHERE shop_domain = :shop AND event_type = 'purchase'"
    ), {"shop": shop}).fetchone()
    has_purchase_events = (event_row[0] or 0) > 0

    # Get the pixel code
    app_url = os.getenv("APP_URL", "")
    from app.models.merchant import Merchant
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    pixel_secret = merchant.pixel_secret if merchant else ""

    pixel_code = ""
    if app_url and pixel_secret:
        pixel_code = f'''var API_URL      = "{app_url}/track";
var SHOP_DOMAIN  = "{shop}";
var PIXEL_SECRET = "{pixel_secret}";

analytics.subscribe("checkout_completed", function (event) {{
  try {{
    var checkout = event.data.checkout;
    if (!checkout) return;
    var orderId = "";
    if (checkout.order && checkout.order.id) orderId = String(checkout.order.id);
    else if (checkout.orderId) orderId = String(checkout.orderId);
    else if (checkout.token) orderId = "tok_" + String(checkout.token);
    if (!orderId) return;
    var gidMatch = orderId.match(/\\/(\\d+)$/);
    if (gidMatch) orderId = gidMatch[1];
    var orderTotal = 0;
    var currency = "EUR";
    if (checkout.totalPrice && typeof checkout.totalPrice === "object") {{
      orderTotal = parseFloat(checkout.totalPrice.amount);
      currency = (checkout.totalPrice.currencyCode || "EUR").toUpperCase();
    }} else if (checkout.totalPrice) orderTotal = parseFloat(checkout.totalPrice);
    if (isNaN(orderTotal) || orderTotal <= 0) return;
    var dedupKey = "hs_purchase_" + orderId;
    try {{ if (browser && browser.localStorage) {{ var already = browser.localStorage.getItem(dedupKey); if (already) return; browser.localStorage.setItem(dedupKey, "1"); }} }} catch (_) {{}}
    var visitorId = event.clientId ? String(event.clientId) : "pixel_" + orderId;
    var trackerVisitorId = "";
    try {{ if (typeof browser !== "undefined" && browser.cookie) {{ var cookieVal = browser.cookie.get("_hs_vid"); if (cookieVal) trackerVisitorId = decodeURIComponent(String(cookieVal)); }} }} catch (_) {{}}
    var pageUrl = "";
    try {{ if (event.context && event.context.document && event.context.document.location) pageUrl = event.context.document.location.href || ""; }} catch (_) {{}}
    fetch(API_URL, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ shop_domain: SHOP_DOMAIN, visitor_id: visitorId, event_type: "purchase", page_url: pageUrl, timestamp: Date.now(), order_id: orderId, order_total: orderTotal, currency: currency, tracker_visitor_id: trackerVisitorId || undefined, pixel_secret: PIXEL_SECRET }}),
      mode: "cors"
    }}).catch(function () {{}});
  }} catch (_) {{}}
}});'''

    return {
        "pixel_active": pixel_active or has_purchase_events,
        "orders_from_pixel": row[0] or 0,
        "purchase_events": event_row[0] or 0,
        "pixel_code": pixel_code,
        "instructions": [
            "Go to Shopify Admin → Settings → Customer events",
            "Click 'Add custom pixel'",
            "Name it 'HedgeSpark'",
            "Paste the code below",
            "Click Save, then Connect",
        ],
    }


# ---------------------------------------------------------------------------
# POST /setup/repair/webhook
# ---------------------------------------------------------------------------

@router.post("/repair/webhook")
async def repair_webhook(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """
    Idempotently re-register the orders/updated webhook with Shopify.

    Safe to call repeatedly — will not create duplicates.
    Updates merchant.webhook_id and webhook_registered_at on success.
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    if merchant is None:
        return JSONResponse(
            status_code=404,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "Merchant not found. App must be installed first.",
            },
        )

    if merchant.install_status != "active":
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "Merchant has uninstalled the app. Reinstall required before repair.",
            },
        )

    if not merchant.access_token:
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "No access token stored. Reinstall required.",
            },
        )

    plaintext_token = decrypt_token(merchant.access_token)
    if not plaintext_token:
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "Access token cannot be decrypted. "
                              "Check MERCHANT_TOKEN_ENCRYPTION_KEY or reinstall.",
            },
        )

    if not _APP_URL:
        return JSONResponse(
            status_code=503,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "APP_URL not configured — cannot determine webhook target URL.",
            },
        )

    # Idempotent register
    webhook_id, was_created = await ensure_orders_webhook(shop, plaintext_token, _APP_URL)

    if webhook_id is None:
        log.error("setup: webhook repair failed shop=%s", shop)
        return JSONResponse(
            status_code=502,
            content={
                "repaired":   False,
                "webhook_id": None,
                "already_ok": False,
                "detail":     "Shopify API call failed. Check logs or retry.",
            },
        )

    # Persist the (possibly new) webhook_id
    now = _now_naive()
    merchant.webhook_id           = webhook_id
    merchant.webhook_registered_at = now
    try:
        db.commit()
    except Exception as exc:
        log.error("setup: failed to persist webhook_id after repair shop=%s: %s", shop, exc)
        db.rollback()
        # Non-fatal — repair succeeded on Shopify side; DB will heal on next deep audit

    already_ok = not was_created
    log.info(
        "setup: webhook repair shop=%s webhook_id=%s already_ok=%s",
        shop, webhook_id, already_ok,
    )

    return {
        "repaired":   True,
        "webhook_id": webhook_id,
        "already_ok": already_ok,
        "detail":     (
            "Webhook was already registered correctly."
            if already_ok
            else f"Webhook registered successfully (id={webhook_id})."
        ),
    }


# ---------------------------------------------------------------------------
# POST /setup/repair/tracker
# ---------------------------------------------------------------------------

@router.post("/repair/tracker")
async def repair_tracker(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """
    Idempotently re-inject spark-tracker.js as a Shopify Script Tag.

    Safe to call repeatedly — will not create duplicates.
    Updates merchant.script_tag_id and script_tag_installed_at on success.
    """
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    if merchant is None:
        return JSONResponse(
            status_code=404,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "Merchant not found. App must be installed first.",
            },
        )

    if merchant.install_status != "active":
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "Merchant has uninstalled the app. Reinstall required before repair.",
            },
        )

    if not merchant.access_token:
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "No access token stored. Reinstall required.",
            },
        )

    plaintext_token = decrypt_token(merchant.access_token)
    if not plaintext_token:
        return JSONResponse(
            status_code=409,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "Access token cannot be decrypted. "
                              "Check MERCHANT_TOKEN_ENCRYPTION_KEY or reinstall.",
            },
        )

    t_url = _tracker_url()
    if not t_url:
        return JSONResponse(
            status_code=503,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "APP_URL not configured — cannot determine tracker script URL.",
            },
        )

    # Idempotent inject
    script_tag_id, was_created = await ensure_tracker_script_tag(shop, plaintext_token, t_url)

    if script_tag_id is None:
        log.error("setup: tracker repair failed shop=%s", shop)
        return JSONResponse(
            status_code=502,
            content={
                "repaired":   False,
                "tracker_id": None,
                "already_ok": False,
                "detail":     "Shopify API call failed. Check logs or retry.",
            },
        )

    # Persist the (possibly new) script_tag_id
    now = _now_naive()
    merchant.script_tag_id          = script_tag_id
    merchant.script_tag_installed_at = now
    try:
        db.commit()
    except Exception as exc:
        log.error("setup: failed to persist script_tag_id after repair shop=%s: %s", shop, exc)
        db.rollback()

    already_ok = not was_created
    log.info(
        "setup: tracker repair shop=%s tracker_id=%s already_ok=%s",
        shop, script_tag_id, already_ok,
    )

    return {
        "repaired":   True,
        "tracker_id": script_tag_id,
        "already_ok": already_ok,
        "detail":     (
            "Tracker script tag was already installed correctly."
            if already_ok
            else f"Tracker script tag installed successfully (id={script_tag_id})."
        ),
    }
