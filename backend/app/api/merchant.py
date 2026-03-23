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
Known values in the current schema default to "starter".  To keep the
frontend contract simple, this endpoint normalises the value:

    "pro"  → "pro"
    anything else (starter, lite, free, etc.) → "lite"

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

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_api_key, require_shop
from app.models.merchant import Merchant

router = APIRouter(prefix="/merchant", tags=["merchant"])

_PRO_PLAN = "pro"


def _normalise_plan(raw: str | None) -> str:
    """
    Normalise the raw plan string from the merchants table.

    Returns "pro" only when the stored value is exactly "pro".
    All other values — "starter", "lite", "free", None, or any unknown
    string — map to "lite".
    """
    return _PRO_PLAN if raw == _PRO_PLAN else "lite"


@router.get("/plan")
def get_merchant_plan(
    shop: str = Depends(require_shop),
    _:    None = Depends(require_api_key),
    db:   Session = Depends(get_db),
):
    """
    Return the plan, billing status, and install status for the given shop.

    Reads from the merchants table.  Defaults to lite/False/active when no row
    exists so the frontend always receives a valid, safe response.
    """
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    if row is None:
        return {
            "shop_domain":    shop,
            "plan":           "lite",
            "billing_active": False,
            "install_status": "active",
        }

    return {
        "shop_domain":    shop,
        "plan":           _normalise_plan(row.plan),
        "billing_active": bool(row.billing_active),
        "install_status": row.install_status or "active",
    }
