"""
billing.py — Shopify Billing API integration.

Implements the full RecurringApplicationCharge lifecycle:
  - Create a pending subscription charge
  - Handle the merchant-facing confirmation callback
  - Activate confirmed charges
  - Persist billing state to the merchants table

Endpoints
---------
POST /billing/subscribe?shop=<domain>
    Initiates a Pro plan subscription request.
    Requires a valid merchant row with a usable access_token.
    Creates a Shopify RecurringApplicationCharge and returns the
    confirmation_url the frontend must redirect the merchant to.

    Idempotent: if a pending charge_id already exists for this shop it is
    re-fetched and the confirmation_url returned without creating a new charge.

GET /billing/callback?charge_id=<id>&shop=<domain>
    Called by Shopify after the merchant accepts or declines the billing page.
    Fetches the charge status from Shopify's API, then:

      "accepted"  → activates the charge → sets plan="pro", billing_active=True,
                    billing_confirmed_at=now, billing_charge_id=charge_id
                    → redirects to DASHBOARD_URL/?billing=activated
      "declined"  → clears billing_charge_id → plan stays "lite"
                    → redirects to DASHBOARD_URL/?billing=declined
      "pending"   → no state change → redirects with billing=pending
      any other   → redirects with billing=error

    Idempotent: if billing_active is already True and the charge_id matches,
    the merchant is redirected immediately without re-calling Shopify.

Billing state on the merchant row
----------------------------------
    billing_charge_id    — Shopify charge ID (string; may exceed int32).
                           Set on subscribe (pending). Cleared on decline.
    billing_confirmed_at — Timestamp of activation acceptance.
    billing_active       — True only when charge is active.
    plan                 — Set to "pro" on activation.

Security
--------
    POST /billing/subscribe: requires X-API-Key (DASHBOARD_API_KEY).
    GET  /billing/callback:  no API key — Shopify redirects the merchant's
                             browser here.  Security comes from fetching charge
                             status directly from Shopify API rather than
                             trusting URL parameters.

Environment variables
---------------------
    SHOPIFY_PRO_PLAN_PRICE   float   Subscription price in USD (default: 29.00)
    SHOPIFY_PRO_PLAN_NAME    str     Charge name shown on Shopify billing page
                                     (default: "Hedge Spark Pro")
    SHOPIFY_PRO_TRIAL_DAYS   int     Free trial days (default: 14)
    APP_URL                  str     Backend base URL — used to build return_url
    DASHBOARD_URL            str     Frontend URL — redirect destination after callback
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_api_key, require_shop
from app.core.token_crypto import decrypt_token
from app.models.merchant import Merchant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

# ---------------------------------------------------------------------------
# Configuration — read at import time; restart required after change
# ---------------------------------------------------------------------------

_APP_URL:       str   = os.getenv("APP_URL",               "").rstrip("/")
_DASHBOARD_URL: str   = os.getenv("DASHBOARD_URL",         "").rstrip("/")
_PRO_PRICE:     float = float(os.getenv("SHOPIFY_PRO_PLAN_PRICE",  "29.00"))
_PRO_NAME:      str   = os.getenv("SHOPIFY_PRO_PLAN_NAME", "Hedge Spark Pro")
_TRIAL_DAYS:    int   = int(os.getenv("SHOPIFY_PRO_TRIAL_DAYS", "14"))

_SHOPIFY_API_VERSION = "2024-01"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_merchant(db: Session, shop_domain: str) -> Optional[Merchant]:
    return db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()


def _get_access_token(merchant: Merchant) -> Optional[str]:
    """Return the decrypted access token, or None if unavailable."""
    if not merchant.access_token:
        return None
    decrypted = decrypt_token(merchant.access_token)
    if decrypted is None:
        log.error("billing: token decryption failed shop=%s", merchant.shop_domain)
    return decrypted


def _shopify_headers(token: str) -> dict:
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type":           "application/json",
    }


async def _create_charge(shop: str, token: str) -> Optional[dict]:
    """
    Call POST /recurring_application_charges and return the charge object,
    or None on failure.
    """
    if not _APP_URL:
        log.error("billing: APP_URL not configured — cannot build return_url")
        return None

    return_url = f"{_APP_URL}/billing/callback"
    payload = {
        "recurring_application_charge": {
            "name":        _PRO_NAME,
            "price":       _PRO_PRICE,
            "return_url":  return_url,
            "test":        False,
            "trial_days":  _TRIAL_DAYS,
        }
    }
    url = f"https://{shop}/admin/api/{_SHOPIFY_API_VERSION}/recurring_application_charges.json"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=_shopify_headers(token))
        if resp.status_code not in (200, 201):
            log.error(
                "billing: create_charge failed shop=%s status=%d body=%s",
                shop, resp.status_code, resp.text[:300],
            )
            return None
        data   = resp.json()
        charge = data.get("recurring_application_charge")
        if charge:
            log.info(
                "billing: charge created shop=%s charge_id=%s status=%s",
                shop, charge.get("id"), charge.get("status"),
            )
        return charge
    except Exception as exc:
        log.error("billing: create_charge exception shop=%s: %s", shop, exc)
        return None


async def _fetch_charge(shop: str, token: str, charge_id: str) -> Optional[dict]:
    """
    Call GET /recurring_application_charges/{id} and return the charge object.
    """
    url = (
        f"https://{shop}/admin/api/{_SHOPIFY_API_VERSION}"
        f"/recurring_application_charges/{charge_id}.json"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=_shopify_headers(token))
        if resp.status_code == 404:
            log.warning("billing: charge not found shop=%s charge_id=%s", shop, charge_id)
            return None
        if resp.status_code != 200:
            log.error(
                "billing: fetch_charge failed shop=%s charge_id=%s status=%d",
                shop, charge_id, resp.status_code,
            )
            return None
        return resp.json().get("recurring_application_charge")
    except Exception as exc:
        log.error("billing: fetch_charge exception shop=%s charge_id=%s: %s", shop, charge_id, exc)
        return None


async def _activate_charge(shop: str, token: str, charge_id: str) -> bool:
    """
    Call POST /recurring_application_charges/{id}/activate.
    Returns True on success.
    """
    url = (
        f"https://{shop}/admin/api/{_SHOPIFY_API_VERSION}"
        f"/recurring_application_charges/{charge_id}/activate.json"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json={}, headers=_shopify_headers(token))
        if resp.status_code in (200, 201):
            log.info("billing: charge activated shop=%s charge_id=%s", shop, charge_id)
            return True
        log.error(
            "billing: activate failed shop=%s charge_id=%s status=%d body=%s",
            shop, charge_id, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        log.error("billing: activate exception shop=%s charge_id=%s: %s", shop, charge_id, exc)
        return False


def _redirect(path: str) -> RedirectResponse:
    """Build a redirect to the dashboard, falling back to a JSON response when DASHBOARD_URL is absent."""
    if _DASHBOARD_URL:
        return RedirectResponse(url=f"{_DASHBOARD_URL}{path}", status_code=302)
    # Dev fallback — no redirect configured
    return JSONResponse({"billing": path.lstrip("/?billing="), "detail": "DASHBOARD_URL not configured"})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/subscribe")
async def subscribe(
    shop: str = Depends(require_shop),
    _:    None = Depends(require_api_key),
    db:   Session = Depends(get_db),
):
    """
    Create a pending Shopify RecurringApplicationCharge for the given shop.

    Returns JSON:
        {
            "confirmation_url": "https://...",
            "charge_id":        "12345678"
        }

    The frontend must redirect the merchant to confirmation_url so they can
    approve the charge on Shopify's billing page.  On return, Shopify calls
    GET /billing/callback?charge_id=<id>&shop=<domain>.

    Idempotent: if billing_charge_id is already set and the charge is still
    pending, the existing confirmation_url is returned rather than creating a
    new charge.
    """
    merchant = _get_merchant(db, shop)
    if merchant is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "Merchant not found. Complete app installation first."},
        )

    if merchant.install_status == "uninstalled":
        return JSONResponse(
            status_code=409,
            content={"detail": "Merchant has uninstalled the app. Reinstall required."},
        )

    # Short-circuit: already Pro and billing is active
    if merchant.plan == "pro" and merchant.billing_active:
        return JSONResponse(
            status_code=200,
            content={"detail": "Shop is already on the Pro plan.", "plan": "pro"},
        )

    token = _get_access_token(merchant)
    if not token:
        return JSONResponse(
            status_code=409,
            content={"detail": "Access token not available — merchant may need to reinstall."},
        )

    # Idempotent: re-use pending charge if one already exists
    if merchant.billing_charge_id:
        charge = await _fetch_charge(shop, token, merchant.billing_charge_id)
        if charge and charge.get("status") == "pending":
            confirmation_url = charge.get("confirmation_url")
            if confirmation_url:
                log.info(
                    "billing: returning existing pending charge shop=%s charge_id=%s",
                    shop, merchant.billing_charge_id,
                )
                return {
                    "confirmation_url": confirmation_url,
                    "charge_id":        str(merchant.billing_charge_id),
                }
        # Charge is stale (declined/expired) — fall through to create a new one
        log.info(
            "billing: existing charge is not pending shop=%s — creating new charge",
            shop,
        )

    # Create new charge
    charge = await _create_charge(shop, token)
    if not charge:
        return JSONResponse(
            status_code=502,
            content={"detail": "Failed to create billing charge with Shopify. Please try again."},
        )

    confirmation_url = charge.get("confirmation_url")
    charge_id        = str(charge.get("id", ""))

    if not confirmation_url or not charge_id:
        log.error("billing: charge response missing expected fields shop=%s: %s", shop, charge)
        return JSONResponse(
            status_code=502,
            content={"detail": "Unexpected response from Shopify billing API."},
        )

    # Persist pending charge_id — billing not yet confirmed
    merchant.billing_charge_id = charge_id
    try:
        db.commit()
    except Exception as exc:
        log.error("billing: failed to persist billing_charge_id shop=%s: %s", shop, exc)
        db.rollback()
        # Non-fatal — return the URL; worst case the charge_id isn't stored
        # and the callback will look it up from the URL param directly

    return {
        "confirmation_url": confirmation_url,
        "charge_id":        charge_id,
    }


@router.get("/callback")
async def billing_callback(
    charge_id: str,
    shop:      str,
    db:        Session = Depends(get_db),
):
    """
    Shopify billing confirmation callback.

    Called by Shopify when the merchant accepts or declines the charge.
    Fetches charge status from Shopify API and updates merchant state.

    Redirects to DASHBOARD_URL/?billing=activated|declined|pending|error&shop=<domain>
    """
    merchant = _get_merchant(db, shop)
    if merchant is None:
        log.error("billing: callback for unknown shop=%s charge_id=%s", shop, charge_id)
        return _redirect(f"/?billing=error&shop={shop}")

    # Idempotent: already confirmed billing for this charge
    if merchant.billing_active and merchant.billing_charge_id == charge_id:
        log.info("billing: callback for already-active charge shop=%s charge_id=%s", shop, charge_id)
        return _redirect(f"/?billing=activated&shop={shop}")

    token = _get_access_token(merchant)
    if not token:
        log.error("billing: callback — no access token shop=%s", shop)
        return _redirect(f"/?billing=error&shop={shop}")

    # Fetch authoritative charge status from Shopify
    charge = await _fetch_charge(shop, token, charge_id)
    if charge is None:
        log.error("billing: could not fetch charge shop=%s charge_id=%s", shop, charge_id)
        return _redirect(f"/?billing=error&shop={shop}")

    status = charge.get("status", "")
    log.info("billing: callback shop=%s charge_id=%s status=%s", shop, charge_id, status)

    if status == "accepted":
        # Activate the charge with Shopify
        activated = await _activate_charge(shop, token, charge_id)
        if not activated:
            return _redirect(f"/?billing=error&shop={shop}")

        # Update merchant to Pro
        now = _now_naive()
        merchant.plan                 = "pro"
        merchant.billing_active       = True
        merchant.billing_charge_id    = charge_id
        merchant.billing_confirmed_at = now
        try:
            db.commit()
            log.info("billing: merchant upgraded to Pro shop=%s charge_id=%s", shop, charge_id)
        except Exception as exc:
            log.error("billing: failed to persist Pro upgrade shop=%s: %s", shop, exc)
            db.rollback()
            return _redirect(f"/?billing=error&shop={shop}")

        return _redirect(f"/?billing=activated&shop={shop}")

    elif status == "declined":
        # Clear the pending charge — merchant chose not to subscribe
        merchant.billing_charge_id = None
        try:
            db.commit()
        except Exception as exc:
            log.error("billing: failed to clear declined charge shop=%s: %s", shop, exc)
            db.rollback()
        log.info("billing: charge declined shop=%s", shop)
        return _redirect(f"/?billing=declined&shop={shop}")

    elif status == "pending":
        # Merchant hasn't completed the flow yet (rare — Shopify normally only
        # calls this URL after a decision is made)
        log.warning("billing: callback received but charge still pending shop=%s", shop)
        return _redirect(f"/?billing=pending&shop={shop}")

    else:
        # expired, frozen, cancelled, or unknown
        log.warning("billing: unexpected charge status=%s shop=%s", status, shop)
        merchant.billing_charge_id = None
        try:
            db.commit()
        except Exception:
            db.rollback()
        return _redirect(f"/?billing=error&shop={shop}")
