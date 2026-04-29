"""
merchant.py — /merchant/plan endpoint.

Returns the plan, billing status, and install status for a shop, read from the
merchants table.  This is the authoritative source for frontend tier gating.
The URL-parameter approach (?plan=pro) used during early development is not
production-safe — any visitor can append it.  This endpoint replaces it.

Request
-------
    GET /merchant/plan?shop=<shop_domain>
    Headers: X-API-Key (when DASHBOARD_API_KEY is configured)

Response
--------
    200 OK — JSON dict:

    shop_domain      str   the validated shop domain
    plan             str   "lite" or "pro"
    billing_active   bool  true when the billing subscription is active
    install_status   str   "active" or "uninstalled"

    400 if shop param is missing or invalid (from require_shop).

Plan normalisation
------------------
merchants.plan stores the raw plan string set at install/upgrade time.
Known values in the current schema default to "lite".  To keep the
frontend contract simple, this endpoint normalises the value:

    "pro"  → "pro"
    anything else ("lite", "free", legacy values, None) → "lite"

This means adding a new plan tier in the future requires only a change
here, not in every frontend component that checks the plan.

Missing merchant row
--------------------
If no row exists in merchants for the given shop_domain (e.g. the shop
connected before the OAuth flow wrote a row, or in a test environment),
the endpoint returns plan="lite", billing_active=False, install_status="active"
rather than 404.  This fail-safe default ensures the frontend always renders
a valid gated state and never shows Pro features to an unverified shop.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.models.merchant import Merchant

router = APIRouter(prefix="/merchant", tags=["merchant"])

_PRO_PLAN = "pro"

# Billing config — surfaced to the frontend so CTA copy stays truthful.
# Read once at import time (same pattern as billing.py).
_PRO_PRICE:      float = float(os.getenv("SHOPIFY_PRO_PLAN_PRICE",  "49.00"))
_PRO_TRIAL_DAYS: int   = int(os.getenv("SHOPIFY_PRO_TRIAL_DAYS", "14"))


def _normalise_plan(raw: str | None) -> str:
    """
    Normalise the raw plan string from the merchants table.

    Returns "pro" only when the stored value is exactly "pro".
    All other values — "lite", "free", legacy values, None, or any
    unknown string — map to "lite".
    """
    return _PRO_PLAN if raw == _PRO_PLAN else "lite"


class MerchantMeResponse(BaseModel):
    """GET /merchant/me — session bootstrap identity + plan payload."""
    shop_domain: str
    pro_trial_days: int
    pro_price: float
    plan: str
    billing_active: bool
    install_status: str
    billing_confirmed_at: str | None = None


@router.get(
    "/me",
    response_model=MerchantMeResponse,
    response_model_exclude_none=False,
)
def get_merchant_me(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """
    Return the authenticated merchant's identity and plan from the session cookie.

    This is the session bootstrap endpoint.  The frontend calls it on page load
    (no ?shop= parameter needed) to discover which shop is logged in.

    Returns 401 if no valid session exists — the frontend shows the
    "no shop connected" state and directs the merchant to install.

    Response shape matches /merchant/plan for backward compatibility.
    """
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    base = {
        "shop_domain":     shop,
        "pro_trial_days":  _PRO_TRIAL_DAYS,
        "pro_price":       _PRO_PRICE,
    }

    if row is None:
        return {
            **base,
            "plan":              "lite",
            "billing_active":    False,
            "install_status":    "active",
            "billing_confirmed_at": None,
        }

    return {
        **base,
        "plan":              _normalise_plan(row.plan),
        "billing_active":    bool(row.billing_active),
        "install_status":    row.install_status or "active",
        "billing_confirmed_at": (
            row.billing_confirmed_at.isoformat() + "Z"
            if row.billing_confirmed_at else None
        ),
    }


# Keep /merchant/plan as an alias for backward compatibility
# (same handler, same auth, same response)
@router.get(
    "/plan",
    response_model=MerchantMeResponse,
    response_model_exclude_none=False,
)
def get_merchant_plan(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """Alias for /merchant/me — kept for backward compatibility."""
    return get_merchant_me(shop=shop, db=db)


@router.get("/activation")
def get_merchant_activation(
    shop: str = Depends(require_merchant_session),
    db:   Session = Depends(get_db),
):
    """Return the activation stage classification for this merchant."""
    from app.services.activation import classify_activation
    return classify_activation(db, shop)
