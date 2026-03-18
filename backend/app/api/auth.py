"""
Shopify OAuth install and callback endpoints for Hedge Spark.

Flow:
  GET /install?shop=example.myshopify.com
      → validates shop
      → redirects merchant to Shopify authorization page

  GET /auth/callback?shop=...&code=...&hmac=...
      → verifies HMAC
      → exchanges code for permanent access token
      → upserts Merchant row (create on first install, update token on reinstall)
      → redirects merchant to /dashboard?shop=<shop_domain>
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.merchant import Merchant
from app.services.shopify_auth import (
    APP_URL,
    build_install_url,
    exchange_code_for_token,
    is_valid_shop_domain,
    verify_hmac,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# GET /install
# ---------------------------------------------------------------------------

@router.get("/install")
def install(shop: str = Query(..., description="Shopify shop domain")):
    """
    Entry point for the Shopify app install flow.

    Validates the shop domain and redirects the merchant to the Shopify
    OAuth authorization page where they grant permissions to Hedge Spark.
    """
    if not is_valid_shop_domain(shop):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop domain. Must be a valid *.myshopify.com address.",
        )

    install_url = build_install_url(shop)
    logger.info("Install initiated for shop=%s", shop)
    return RedirectResponse(url=install_url)


# ---------------------------------------------------------------------------
# GET /auth/callback
# ---------------------------------------------------------------------------

@router.get("/auth/callback")
def auth_callback(
    shop: str = Query(...),
    code: str = Query(...),
    hmac: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Shopify OAuth callback.

    1. Validates the shop domain.
    2. Verifies the HMAC Shopify attached to the redirect.
    3. Exchanges the one-time code for a permanent access token.
    4. Creates or updates the Merchant record.
    5. Redirects the merchant to the dashboard.
    """
    # Step 1 — validate shop
    if not is_valid_shop_domain(shop):
        raise HTTPException(status_code=400, detail="Invalid shop domain.")

    # Step 2 — verify HMAC using all query params received
    # Build the param dict that was actually sent by Shopify (shop, code, hmac
    # plus any others Shopify may append). We reconstruct it from the known
    # required params; FastAPI will pass unknown extras as **kwargs if needed,
    # but the HMAC check only requires the keys Shopify sends. We explicitly
    # pass the three required params and let the service filter out 'hmac'.
    raw_params = {"shop": shop, "code": code, "hmac": hmac}
    if not verify_hmac(raw_params, hmac):
        logger.warning("HMAC verification failed for shop=%s", shop)
        raise HTTPException(status_code=403, detail="HMAC verification failed.")

    # Step 3 — exchange code for access token
    try:
        access_token = exchange_code_for_token(shop, code)
    except httpx.HTTPStatusError as exc:
        logger.error("Token exchange failed for shop=%s: %s", shop, exc)
        raise HTTPException(
            status_code=502,
            detail="Failed to obtain access token from Shopify.",
        )
    except KeyError:
        logger.error("Shopify token response missing access_token for shop=%s", shop)
        raise HTTPException(
            status_code=502,
            detail="Unexpected response from Shopify token endpoint.",
        )

    # Step 4 — upsert Merchant
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()

    if merchant is None:
        merchant = Merchant(
            shop_domain=shop,
            access_token=access_token,
            plan="starter",
            installed_at=datetime.utcnow(),
            billing_active=False,
        )
        db.add(merchant)
        logger.info("New merchant created: shop=%s", shop)
    else:
        merchant.access_token = access_token
        logger.info("Merchant token updated: shop=%s", shop)

    db.commit()

    # Step 5 — redirect to dashboard
    dashboard_url = f"{APP_URL}/dashboard?shop={shop}"
    return RedirectResponse(url=dashboard_url)
