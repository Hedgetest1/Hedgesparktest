"""
Shared FastAPI dependencies for Hedge Spark.

require_shop    — extracts and validates shop_domain from the request
require_api_key — validates the DASHBOARD_API_KEY header

Exemptions (applied at the route level, not here):
  POST /track        — tracker.js write endpoint, no auth header
  GET  /tracker.js   — static file
  GET  /install      — Shopify OAuth entry
  GET  /auth/callback — Shopify OAuth callback
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, Query

from app.services.shopify_auth import is_valid_shop_domain

DASHBOARD_API_KEY: str = os.getenv("DASHBOARD_API_KEY", "")


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
    If DASHBOARD_API_KEY is not configured the check is skipped (dev mode).
    """
    if not DASHBOARD_API_KEY:
        return
    if x_api_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
