"""
Shared FastAPI dependencies for WishSpark.

Available dependencies
----------------------
get_db                  — yields a request-scoped SQLAlchemy session (pool-safe)
require_shop            — extracts and validates shop_domain from the request
require_merchant_session — authenticates merchant via httpOnly session cookie
require_pro_session     — session auth + Pro plan enforcement

Auth model (post-hardening)
---------------------------
All dashboard endpoints use require_merchant_session, which:
  1. Reads the hs_session httpOnly cookie
  2. Verifies the JWT signature + expiry
  3. Checks session_version against the merchant row (forced logout support)
  4. Returns shop_domain from the verified token

There is NO API key fallback for browser requests.  The only non-cookie
auth path is ALLOW_INSECURE_DEV for local development (hard-fails in
production-like environments — see main.py startup audit).

Storefront-facing endpoints (/track, /nudges/active, /nudge/event,
/tracker.js, /webhooks/*) use require_shop or no auth — they serve
public storefront traffic and are rate-limited instead.
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

_ALLOW_INSECURE_DEV: bool = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"


def require_shop(
    shop: str | None = Query(default=None, alias="shop"),
    x_shop_domain: str | None = Header(default=None, alias="X-Shop-Domain"),
) -> str:
    """
    Return shop_domain from the ?shop= query param or X-Shop-Domain header.
    Raises 400 if missing or invalid.
    Used by storefront-facing endpoints only.
    """
    domain = shop or x_shop_domain
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Missing shop_domain. Pass ?shop=<domain> or X-Shop-Domain header.",
        )
    if not is_valid_shop_domain(domain):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com address.",
        )
    return domain


def require_merchant_session(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """
    Authenticate the merchant via session cookie.

    Reads the hs_session httpOnly cookie, verifies the JWT, then checks
    the session_version claim against the merchant row.  If the merchant
    has bumped their session_version (e.g. after a forced logout), all
    tokens with the old version are rejected.

    Returns shop_domain on success.  Raises 401 on failure.

    The ?shop= query param is IGNORED for authentication.  Shop identity
    comes exclusively from the signed, httpOnly cookie.
    """
    from app.core.merchant_session import SESSION_COOKIE_NAME, verify_session_token

    # Path 1: session cookie (only production path)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        payload = verify_session_token(session_token)
        if payload:
            shop = payload["shop"]
            token_sv = payload.get("sv", 0)

            # Check session_version against DB — enables forced logout
            from app.models.merchant import Merchant
            merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
            if merchant is not None:
                db_sv = getattr(merchant, "session_version", None) or 0
                if token_sv < db_sv:
                    log.warning(
                        "deps: session rejected — token sv=%d < merchant sv=%d for shop=%s",
                        token_sv, db_sv, shop,
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Session expired. Please log in again.",
                    )
            return shop
        # Cookie exists but is invalid/expired

    # Path 2: insecure dev bypass (ONLY in dev, hard-killed in production by main.py)
    if _ALLOW_INSECURE_DEV:
        shop_param = request.query_params.get("shop")
        if shop_param and is_valid_shop_domain(shop_param):
            return shop_param

    raise HTTPException(status_code=401, detail="Authentication required.")


def require_pro_session(
    request: Request,
    db: Session = Depends(get_db),
) -> str:
    """
    Authenticate merchant session AND enforce Pro plan.

    Combines require_merchant_session + Pro plan check.
    Returns shop_domain on success.  Raises 401 or 403 on failure.
    """
    shop = require_merchant_session(request, db)

    from app.models.merchant import Merchant
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if row is None or row.plan != "pro" or not row.billing_active:
        raise HTTPException(status_code=403, detail="Pro plan required.")

    return shop


# ---------------------------------------------------------------------------
# Operator access — internal API key auth for admin/ops endpoints
# ---------------------------------------------------------------------------

_OPERATOR_KEY: str = os.getenv("DASHBOARD_API_KEY", "").strip()
_OPERATOR_KEY_PREV: str = os.getenv("DASHBOARD_API_KEY_PREV", "").strip()


def require_operator(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """
    Authenticate operator access via X-API-Key header.

    Accepts DASHBOARD_API_KEY (primary) or DASHBOARD_API_KEY_PREV (rotation
    window). During key rotation, set the new key as primary and the old key
    as _PREV. After all clients are updated, remove _PREV.

    Returns True on success.  Raises 401 on failure.
    """
    if not _OPERATOR_KEY:
        raise HTTPException(status_code=503, detail="Operator access not configured.")
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid operator key.")
    if x_api_key == _OPERATOR_KEY:
        return True
    if _OPERATOR_KEY_PREV and x_api_key == _OPERATOR_KEY_PREV:
        return True
    raise HTTPException(status_code=401, detail="Invalid operator key.")
