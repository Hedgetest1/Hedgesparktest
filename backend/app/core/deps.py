"""
Shared FastAPI dependencies for WishSpark.

Available dependencies
----------------------
get_db              — yields a request-scoped SQLAlchemy session (pool-safe)
require_shop        — extracts and validates shop_domain from the request
require_api_key     — validates the DASHBOARD_API_KEY header
require_pro_plan    — validates shop + API key AND enforces active Pro plan

Plan enforcement
----------------
require_pro_plan is the canonical backend gate for Pro-only routes.
It raises HTTP 403 when the shop's merchants row has:
  - no row at all (unknown shop)
  - plan != "pro"
  - billing_active == False

The Pro definition here — plan == "pro" AND billing_active == True — is the
same semantic used by merchant.py's _normalise_plan() and by the frontend's
isProUser check.  If the Pro definition ever changes, update this file and
merchant.py together; they are the two authoritative sources.

Connection safety
-----------------
require_pro_plan now accepts db: Session = Depends(get_db) so it reuses the
request-scoped session provided by FastAPI's DI system.  This eliminates the
previous anti-pattern of opening a raw SessionLocal() inside the function on
every request, which bypassed the connection pool and caused pool exhaustion
under concurrent load.

To enforce a route as Pro-only, replace:
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
with:
    shop: str = Depends(require_pro_plan),

require_pro_plan composes both checks internally so nothing is skipped.

Exemptions (applied at the route level, not here):
  POST /track         — tracker.js write endpoint, no auth header
  GET  /tracker.js    — static file
  GET  /install       — Shopify OAuth entry
  GET  /auth/callback — Shopify OAuth callback
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

DASHBOARD_API_KEY: str = os.getenv("DASHBOARD_API_KEY", "")

# When ALLOW_INSECURE_DEV=true the API key check is bypassed if the key is
# absent — acceptable only in a private development environment where no real
# merchant data is present.  Production deployments must NEVER set this.
_ALLOW_INSECURE_DEV: bool = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"

# Emit a single message at import time (server startup) so the operator
# can see the auth posture immediately in: pm2 logs wishspark-backend
if not DASHBOARD_API_KEY:
    if _ALLOW_INSECURE_DEV:
        log.warning(
            "SECURITY: DASHBOARD_API_KEY is not set — "
            "API key enforcement is DISABLED because ALLOW_INSECURE_DEV=true. "
            "This must never be used in a production or merchant-facing deployment."
        )
    else:
        log.error(
            "SECURITY: DASHBOARD_API_KEY is not set and ALLOW_INSECURE_DEV is not enabled. "
            "All /pro/* requests will receive HTTP 503 until a key is configured. "
            "Generate a key with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\" "
            "and add it to backend/.env, then run: pm2 reload ecosystem.config.js"
        )


def require_shop(
    shop: str | None = Query(default=None, alias="shop"),
    x_shop_domain: str | None = Header(default=None, alias="X-Shop-Domain"),
) -> str:
    """
    Return shop_domain from the ?shop= query param or X-Shop-Domain header.
    Raises 400 if missing or invalid.
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


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """
    Validate the dashboard API key.

    Behavior when DASHBOARD_API_KEY is not configured:
      - ALLOW_INSECURE_DEV=true  → check is bypassed (dev convenience only)
      - ALLOW_INSECURE_DEV unset → HTTP 503 is returned; Pro endpoints are
        non-functional until a key is configured.  This is the safe default:
        an unconfigured deployment fails closed, not open.
    """
    if not DASHBOARD_API_KEY:
        if _ALLOW_INSECURE_DEV:
            return  # explicit dev-mode bypass — operator opted in
        raise HTTPException(
            status_code=503,
            detail="Service not properly configured. Contact the operator.",
        )
    if x_api_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def require_pro_plan(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> str:
    """
    Enforce that the requesting shop has an active Pro plan.

    Composes require_shop (400 on bad domain), require_api_key (401 on bad
    key), and a request-scoped DB session — all three checks run before the
    plan check.  Raises 403 if the shop is not on an active Pro plan.

    Returns shop_domain on success, matching the return type of require_shop
    so this is a drop-in replacement on any Pro-only route.

    Pro definition (must stay in sync with merchant.py and the frontend):
      merchants.plan == "pro"  AND  merchants.billing_active == True

    DB session
    ----------
    The db session is provided by FastAPI's DI system via Depends(get_db).
    This reuses the request-scoped connection from the pool — it does NOT
    open a new raw SessionLocal() per call.  This was the critical connection
    pool leak fixed in this version.

    Usage — replace both deps on a Pro route with this single dep:
        shop: str = Depends(require_pro_plan),

    To enforce a new Pro endpoint, add only this one dependency.  No other
    changes are required.
    """
    from app.models.merchant import Merchant

    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    # No row → shop not installed or unknown → deny with same error as expired plan.
    # Pro definition: plan == "pro" AND billing_active == True.
    # This mirrors _normalise_plan() in merchant.py — keep them in sync.
    if row is None or row.plan != "pro" or not row.billing_active:
        raise HTTPException(
            status_code=403,
            detail="Pro plan required.",
        )

    return shop
