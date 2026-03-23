"""
shopify_oauth.py — Shopify OAuth 2.0 install flow.

Endpoints
---------
GET /auth/install?shop=<myshopify-domain>
    Entry point.  Generates a nonce, stores it, then redirects the browser
    to Shopify's OAuth permission screen.

GET /auth/callback?shop=&code=&hmac=&state=&timestamp=
    Shopify redirects here after the merchant grants permission.
    Validates HMAC signature, verifies state nonce, exchanges the code for
    an access token, upserts the merchant record, then redirects to the
    dashboard.

Security model
--------------
HMAC validation:
    All callback parameters (except hmac itself) are sorted alphabetically,
    joined as key=value pairs with "&", then HMAC-SHA256'd with SHOPIFY_API_SECRET.
    The computed digest is compared against the hmac param using
    hmac.compare_digest() to prevent timing attacks.

State nonce:
    A 32-byte cryptographically random hex nonce is generated at install
    time and stored in Redis (TTL: NONCE_TTL_SECS = 300).  The callback
    verifies the nonce is present in the store before consuming and deleting
    it.  This prevents CSRF attacks via replay or injection.

    Redis is the primary store.  When Redis is unavailable, an in-memory
    dict with expiry cleanup is used (adequate for single-process deploys
    without Redis; not adequate for multi-process clusters without Redis).

    IMPORTANT: for multi-process PM2 deploys without Redis, the in-memory
    fallback can fail state validation if the callback is handled by a
    different process than the install request.  Deploy Redis for production.

Token storage:
    The access_token column on the merchants table is nullable varchar.
    For v1 it is stored in plaintext.  For production hardening, encrypt
    with AES-GCM using a MERCHANT_TOKEN_ENCRYPTION_KEY env var before
    writing (see TODO below).

Scopes:
    SHOPIFY_APP_SCOPES env var (default: "read_products,read_orders,write_script_tags")
    read_products     — product catalog for opportunity scoring
    read_orders       — revenue data for AOV and attribution
    write_script_tags — inject spark-tracker.js without theme editor access

    These scopes require Shopify App Partner approval for production use.
    For private apps (single-store), any scopes are available immediately.

Environment variables required
-------------------------------
    SHOPIFY_API_KEY          — App client ID (Shopify Partner Dashboard)
    SHOPIFY_API_SECRET       — App client secret (Shopify Partner Dashboard)
    APP_URL                  — Backend URL for OAuth callback redirect URI
                               e.g. https://api.hedgesparkhq.com
                               Callback URI registered in Shopify:
                                   https://api.hedgesparkhq.com/auth/callback
    DASHBOARD_URL            — Frontend URL for post-install redirect
                               e.g. https://app.hedgesparkhq.com
                               After install: DASHBOARD_URL/?shop=<domain>

Environment variables optional
-------------------------------
    SHOPIFY_APP_SCOPES       — Override default OAuth scopes
    REDIS_URL                — Required for nonce store in multi-process deploy

Shopify Partner Dashboard configuration required
-------------------------------------------------
    Allowed redirection URL(s):  https://api.hedgesparkhq.com/auth/callback
    App URL:                      https://app.hedgesparkhq.com
    (Replace with your actual domains)

Implementation status
---------------------
FULLY IMPLEMENTED:
    - HMAC validation of all callback parameters
    - Nonce generation, storage (Redis + in-memory fallback), and verification
    - Token exchange via httpx (POST to Shopify admin/oauth/access_token)
    - Merchant upsert (create on first install, update token on reinstall)
    - Post-install redirect to DASHBOARD_URL
    - Shop domain format validation
    - Clean error responses on any validation failure

REQUIRES EXTERNAL CONFIGURATION (not code):
    - Shopify Partner account and app creation
    - App API key + secret set in backend/.env
    - Callback URL registered in Shopify Partner Dashboard
    - Shopify App Store review (for public apps)

NOT YET IMPLEMENTED:
    - Access token encryption at rest (plaintext storage in v1)
    - Shopify billing API integration (plan upgrade post-install)
    - Webhook registration at install time (currently manual)
    - Script tag injection at install time (currently manual)
    - Uninstall webhook handler (gdpr/shop/redact and app/uninstalled)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from threading import Lock
from typing import Optional

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.merchant import Merchant
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["shopify-oauth"])

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SHOPIFY_API_KEY:    str = os.getenv("SHOPIFY_API_KEY",    "")
_SHOPIFY_API_SECRET: str = os.getenv("SHOPIFY_API_SECRET", "")
_APP_URL:            str = os.getenv("APP_URL",            "").rstrip("/")
_DASHBOARD_URL:      str = os.getenv("DASHBOARD_URL",      "").rstrip("/")
_APP_SCOPES:         str = os.getenv("SHOPIFY_APP_SCOPES",
                                     "read_products,read_orders,write_script_tags")

# Nonce TTL in seconds — must exceed worst-case OAuth round-trip.
# 5 minutes is generous; Shopify's own OAuth timeout is shorter.
NONCE_TTL_SECS: int = 300

# ---------------------------------------------------------------------------
# Nonce store — Redis primary, in-memory fallback
#
# Key: "hs:oauth_nonce:{shop}:{nonce}"   Value: "1"   TTL: NONCE_TTL_SECS
#
# In-memory fallback: { "shop:nonce" → expiry_monotonic }
# ---------------------------------------------------------------------------

_mem_nonces: dict[str, float] = {}
_mem_nonces_lock: Lock = Lock()


def _nonce_key(shop: str, nonce: str) -> str:
    return f"hs:oauth_nonce:{shop}:{nonce}"


def _store_nonce(shop: str, nonce: str) -> None:
    """Store the nonce in Redis (primary) or in-memory (fallback)."""
    key = _nonce_key(shop, nonce)
    try:
        from app.core.redis_client import _client as redis_client
        client = redis_client()
        if client is not None:
            client.setex(key, NONCE_TTL_SECS, "1")
            return
    except Exception as exc:
        log.warning("shopify_oauth: Redis nonce store failed: %s — using in-memory", exc)

    # In-memory fallback
    with _mem_nonces_lock:
        _mem_nonces[key] = time.monotonic() + NONCE_TTL_SECS
        # Opportunistic cleanup — remove expired entries
        now = time.monotonic()
        expired = [k for k, exp in _mem_nonces.items() if exp < now]
        for k in expired:
            del _mem_nonces[k]


def _consume_nonce(shop: str, nonce: str) -> bool:
    """
    Verify and atomically consume the nonce.

    Returns True if the nonce was valid and has been consumed.
    Returns False if the nonce was not found or has expired.
    A consumed nonce cannot be reused.
    """
    key = _nonce_key(shop, nonce)
    try:
        from app.core.redis_client import _client as redis_client
        client = redis_client()
        if client is not None:
            deleted = client.delete(key)   # atomic: 1 if key existed, 0 if not
            return deleted > 0
    except Exception as exc:
        log.warning("shopify_oauth: Redis nonce consume failed: %s — using in-memory", exc)

    # In-memory fallback
    with _mem_nonces_lock:
        expiry = _mem_nonces.pop(key, None)
        if expiry is None:
            return False
        return time.monotonic() < expiry


# ---------------------------------------------------------------------------
# HMAC validation
# ---------------------------------------------------------------------------

def _validate_hmac(params: dict, provided_hmac: str) -> bool:
    """
    Validate Shopify's HMAC signature on callback parameters.

    Shopify signs the callback by:
    1. Removing the 'hmac' key from the parameter dict.
    2. Sorting all remaining key=value pairs alphabetically.
    3. Joining them with '&'.
    4. Computing HMAC-SHA256 with the app's API secret.

    Uses hmac.compare_digest to prevent timing side-channels.
    Returns False (not raises) when SHOPIFY_API_SECRET is not configured.
    """
    if not _SHOPIFY_API_SECRET:
        log.error("shopify_oauth: SHOPIFY_API_SECRET not configured — HMAC validation impossible")
        return False

    filtered = {k: v for k, v in params.items() if k != "hmac"}
    message   = "&".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    digest    = hmac.new(
        _SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, provided_hmac)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

async def _exchange_code_for_token(shop: str, code: str) -> Optional[str]:
    """
    Exchange the OAuth authorization code for a permanent access token.

    POST https://{shop}/admin/oauth/access_token
    Body: client_id=..., client_secret=..., code=...

    Returns the access_token string on success, None on any error.
    """
    if not _SHOPIFY_API_KEY or not _SHOPIFY_API_SECRET:
        log.error("shopify_oauth: SHOPIFY_API_KEY or SHOPIFY_API_SECRET not set")
        return None

    url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id":     _SHOPIFY_API_KEY,
        "client_secret": _SHOPIFY_API_SECRET,
        "code":          code,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            log.error(
                "shopify_oauth: token exchange failed shop=%s status=%d body=%s",
                shop, resp.status_code, resp.text[:200],
            )
            return None
        data = resp.json()
        token = data.get("access_token")
        scope = data.get("scope", "")
        if token:
            log.info(
                "shopify_oauth: token exchange OK shop=%s scope=%s",
                shop, scope,
            )
        return token or None
    except Exception as exc:
        log.error("shopify_oauth: token exchange exception shop=%s: %s", shop, exc)
        return None


# ---------------------------------------------------------------------------
# Merchant upsert
# ---------------------------------------------------------------------------

def _upsert_merchant(db: Session, shop: str, access_token: str) -> Merchant:
    """
    Create a new merchant row or update the access_token on reinstall.

    On reinstall (merchant already exists), only the access_token is updated.
    plan and billing_active are preserved — do not reset them on reinstall,
    as a Pro subscriber reinstalling should not lose their plan tier.
    """
    from datetime import datetime, timezone
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if row is None:
        row = Merchant(
            shop_domain    = shop,
            access_token   = access_token,
            plan           = "starter",
            billing_active = False,
        )
        db.add(row)
        log.info("shopify_oauth: new merchant created shop=%s", shop)
    else:
        row.access_token = access_token
        log.info("shopify_oauth: merchant access_token refreshed shop=%s plan=%s", shop, row.plan)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/install")
def install(shop: str, response: Response):
    """
    OAuth install entry point.

    Validates the shop domain format, generates a nonce, stores it, then
    redirects the merchant's browser to Shopify's OAuth permission screen.

    The nonce is embedded in the 'state' parameter.  Shopify passes it back
    unchanged in the callback, enabling CSRF verification.

    Requires:
        SHOPIFY_API_KEY  — used as client_id in the OAuth URL
        APP_URL          — used to build the redirect_uri for the callback

    Query params:
        shop (required) — e.g. example.myshopify.com
    """
    if not _SHOPIFY_API_KEY:
        log.error("shopify_oauth: SHOPIFY_API_KEY not configured")
        return Response("App not configured — contact support.", status_code=500)

    if not is_valid_shop_domain(shop):
        log.warning("shopify_oauth: invalid shop domain in install: %r", shop)
        return Response("Invalid shop domain.", status_code=400)

    if not _APP_URL:
        log.error("shopify_oauth: APP_URL not configured")
        return Response("App URL not configured — contact support.", status_code=500)

    nonce        = secrets.token_hex(32)
    redirect_uri = f"{_APP_URL}/auth/callback"

    _store_nonce(shop, nonce)

    oauth_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={_SHOPIFY_API_KEY}"
        f"&scope={_APP_SCOPES}"
        f"&redirect_uri={redirect_uri}"
        f"&state={nonce}"
        f"&grant_options[]=value"
    )

    log.info("shopify_oauth: initiating install shop=%s nonce=%s…", shop, nonce[:8])
    return RedirectResponse(url=oauth_url, status_code=302)


@router.get("/callback")
async def callback(
    shop:      str,
    code:      str,
    hmac:      str,
    state:     str,
    timestamp: Optional[str] = None,
    db:        Session = Depends(get_db),
):
    """
    Shopify OAuth callback.

    Steps:
    1. Validate shop domain format.
    2. Validate HMAC signature of all callback params.
    3. Verify state nonce exists in store and consume it (prevents replay).
    4. Exchange authorization code for permanent access token.
    5. Upsert merchant record with access_token.
    6. Redirect to dashboard.

    All validation failures return HTTP 400 to Shopify's redirect flow.
    The error is logged; the merchant sees a generic "install failed" message.
    """
    # Step 1 — validate shop domain
    if not is_valid_shop_domain(shop):
        log.warning("shopify_oauth: callback rejected — invalid shop domain: %r", shop)
        return Response("Invalid shop domain.", status_code=400)

    # Step 2 — HMAC validation
    # Build the full params dict for HMAC calculation.
    # timestamp may be absent in test environments; include if present.
    callback_params: dict = {"shop": shop, "code": code, "state": state}
    if timestamp:
        callback_params["timestamp"] = timestamp

    if not _validate_hmac(callback_params, hmac):
        log.warning(
            "shopify_oauth: HMAC validation failed shop=%s — possible replay or "
            "CSRF attempt",
            shop,
        )
        return Response("Invalid HMAC signature.", status_code=400)

    # Step 3 — nonce verification (anti-CSRF)
    if not _consume_nonce(shop, state):
        log.warning(
            "shopify_oauth: nonce not found or expired shop=%s state=%s… "
            "— possible CSRF, replay, or TTL expiry",
            shop, state[:8],
        )
        return Response(
            "Install session expired or invalid. Please try installing again.",
            status_code=400,
        )

    # Step 4 — exchange code for token
    access_token = await _exchange_code_for_token(shop, code)
    if not access_token:
        log.error("shopify_oauth: token exchange failed for shop=%s", shop)
        return Response(
            "Failed to complete installation — could not obtain access token. "
            "Please try again.",
            status_code=502,
        )

    # Step 5 — upsert merchant
    try:
        _upsert_merchant(db, shop, access_token)
    except Exception as exc:
        log.error("shopify_oauth: merchant upsert failed shop=%s: %s", shop, exc)
        return Response(
            "Installation failed — could not save merchant record. "
            "Please try again or contact support.",
            status_code=500,
        )

    # Step 6 — redirect to dashboard
    if _DASHBOARD_URL:
        dest = f"{_DASHBOARD_URL}/?shop={shop}&installed=1"
    else:
        log.warning(
            "shopify_oauth: DASHBOARD_URL not configured — "
            "redirecting to bare success response"
        )
        return {"status": "installed", "shop": shop}

    log.info("shopify_oauth: install complete shop=%s — redirecting to dashboard", shop)
    return RedirectResponse(url=dest, status_code=302)
