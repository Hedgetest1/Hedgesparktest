"""
shopify_oauth.py — Shopify managed install flow (2026 standard).

Architecture
------------
PRIMARY FLOW (managed install):
  1. Merchant clicks "Install" from Shopify / Partner Dashboard
  2. Shopify sends the merchant to our App URL:
         GET /auth/install?shop=...&hmac=...&timestamp=...&host=...
  3. We validate the signed request (when HMAC is present)
  4. We redirect the merchant to Shopify's OAuth consent screen
  5. Shopify redirects back to:
         GET /auth/callback?code=...&hmac=...&shop=...
  6. We exchange the authorization code for a permanent offline token
  7. We encrypt token at rest, upsert merchant, register webhook/script tag,
     set session cookie, redirect to dashboard

SECONDARY FLOW (manual re-auth):
  - /auth/install?shop=STORE.myshopify.com
  - same endpoint, but without incoming Shopify-signed HMAC params
  - useful for reinstalls / re-authorization / scope changes

Shopify Partner Dashboard Configuration
----------------------------------------
  App URL:                      https://api.hedgesparkhq.com/auth/install
  Allowed redirection URL(s):   https://api.hedgesparkhq.com/auth/callback
  Preferences → Embedded:       true or false (works either way)

Environment variables
---------------------
Required:
    SHOPIFY_API_KEY               — App client ID
    SHOPIFY_API_SECRET            — App client secret (used for HMAC + token exchange)
    APP_URL                       — Backend base URL (e.g. https://api.hedgesparkhq.com)
    DASHBOARD_URL                 — Frontend URL  (e.g. https://app.hedgesparkhq.com)

Strongly recommended:
    MERCHANT_TOKEN_ENCRYPTION_KEY — 32-byte hex key for token encryption at rest
    REDIS_URL                     — Required for nonce store in multi-process deploys

Optional:
    SHOPIFY_APP_SCOPES            — Override OAuth scope string
    TRACKER_SCRIPT_URL            — Override tracker URL injected as Script Tag
"""
from __future__ import annotations

import hashlib
import hmac as _hmac_module
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query as QueryParam, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.token_crypto import encrypt_token
from app.models.merchant import Merchant
from app.services.shopify_admin import ensure_orders_webhook, ensure_tracker_script_tag
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["shopify-oauth"])

# ---------------------------------------------------------------------------
# Configuration — read at import time; restart required after change
# ---------------------------------------------------------------------------

_SHOPIFY_API_KEY: str = os.getenv("SHOPIFY_API_KEY", "")
_SHOPIFY_API_SECRET: str = os.getenv("SHOPIFY_API_SECRET", "")
_APP_URL: str = os.getenv("APP_URL", "").rstrip("/")
_DASHBOARD_URL: str = os.getenv("DASHBOARD_URL", "").rstrip("/")
_APP_SCOPES: str = os.getenv(
    "SHOPIFY_APP_SCOPES",
    "read_products,read_orders,write_script_tags",
)
_SHOPIFY_API_VERSION: str = "2024-01"


def _tracker_url() -> str:
    """Return the tracker script URL to register as a Shopify Script Tag."""
    override = os.getenv("TRACKER_SCRIPT_URL", "").strip()
    return override if override else f"{_APP_URL}/tracker.js"


# ---------------------------------------------------------------------------
# Nonce store — Redis primary, in-memory fallback
# ---------------------------------------------------------------------------

_NONCE_TTL: int = 300
_mem_nonces: dict[str, float] = {}
_mem_nonces_lock: Lock = Lock()


def _store_nonce(shop: str, nonce: str) -> None:
    key = f"hs:oauth_nonce:{shop}:{nonce}"
    try:
        from app.core.redis_client import _client as redis_client

        client = redis_client()
        if client is not None:
            client.setex(key, _NONCE_TTL, "1")
            return
    except Exception as exc:
        log.warning("shopify_oauth: Redis nonce store failed: %s — using in-memory", exc)

    with _mem_nonces_lock:
        _mem_nonces[key] = time.monotonic() + _NONCE_TTL
        now = time.monotonic()
        for k in [k for k, exp in _mem_nonces.items() if exp < now]:
            del _mem_nonces[k]


def _consume_nonce(shop: str, nonce: str) -> bool:
    key = f"hs:oauth_nonce:{shop}:{nonce}"
    try:
        from app.core.redis_client import _client as redis_client

        client = redis_client()
        if client is not None:
            return client.delete(key) > 0
    except Exception as exc:
        log.warning("shopify_oauth: Redis nonce consume failed: %s — using in-memory", exc)

    with _mem_nonces_lock:
        expiry = _mem_nonces.pop(key, None)
        if expiry is None:
            return False
        return time.monotonic() < expiry


# ---------------------------------------------------------------------------
# HMAC validation
# ---------------------------------------------------------------------------

def _validate_hmac_from_request(request: Request) -> bool:
    """
    Validate the Shopify HMAC against the raw query string.

    Shopify HMAC spec (https://shopify.dev/docs/apps/auth/oauth#verify-a-request):
      1. Take the raw query string from the URL
      2. Parse into key=value pairs (preserving original URL-encoding)
      3. Remove ONLY the 'hmac' pair
      4. Sort remaining pairs lexicographically by key
      5. Re-join with & separator
      6. HMAC-SHA256 with SHOPIFY_API_SECRET as key
      7. Compare hex digest with the provided 'hmac' value

    CRITICAL: We must parse the RAW query string, NOT use Starlette's
    QueryParams which auto-decodes URL-encoded values (e.g. %3D → =).
    Shopify computes the HMAC over the URL-encoded form, so we must match.
    """
    if not _SHOPIFY_API_SECRET:
        log.error("shopify_oauth: SHOPIFY_API_SECRET not configured — HMAC impossible")
        return False

    # Get the raw query string exactly as the browser sent it
    raw_qs = str(request.url.query) if request.url.query else ""
    if not raw_qs:
        log.warning("shopify_oauth: empty query string — no HMAC possible")
        return False

    # Parse into raw key=value pairs WITHOUT decoding
    provided_hmac = ""
    pairs: list[tuple[str, str]] = []
    for part in raw_qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k == "hmac":
                provided_hmac = v
            else:
                pairs.append((k, v))
        else:
            pairs.append((part, ""))

    if not provided_hmac:
        log.warning("shopify_oauth: no hmac parameter in query string")
        return False

    # Sort lexicographically by key, rejoin
    pairs.sort(key=lambda p: p[0])
    message = "&".join(f"{k}={v}" for k, v in pairs)

    digest = _hmac_module.new(
        _SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    valid = _hmac_module.compare_digest(digest, provided_hmac)
    if not valid:
        log.warning(
            "shopify_oauth: HMAC mismatch — message=%r digest=%s provided=%s",
            message[:200], digest[:16], provided_hmac[:16],
        )
    return valid


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

async def _exchange_code_for_token(shop: str, code: str) -> Optional[str]:
    if not _SHOPIFY_API_KEY or not _SHOPIFY_API_SECRET:
        log.error("shopify_oauth: SHOPIFY_API_KEY or SHOPIFY_API_SECRET not set")
        return None

    url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": _SHOPIFY_API_KEY,
        "client_secret": _SHOPIFY_API_SECRET,
        "code": code,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            log.error(
                "shopify_oauth: token exchange failed shop=%s status=%d body=%s",
                shop,
                resp.status_code,
                resp.text[:200],
            )
            return None
        data = resp.json()
        token = data.get("access_token")
        scope = data.get("scope", "")
        if token:
            log.info("shopify_oauth: token exchange OK shop=%s scope=%s", shop, scope)
        return token or None
    except Exception as exc:
        log.error("shopify_oauth: token exchange exception shop=%s: %s", shop, exc)
        return None


# ---------------------------------------------------------------------------
# Fetch shop owner email from Shopify
# ---------------------------------------------------------------------------

async def _fetch_shop_email(shop: str, token: str) -> Optional[str]:
    """Fetch the shop owner email from Shopify's shop.json API."""
    try:
        url = f"https://{shop}/admin/api/{_SHOPIFY_API_VERSION}/shop.json"
        headers = {"X-Shopify-Access-Token": token}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            email = resp.json().get("shop", {}).get("email")
            if email:
                log.info("shopify_oauth: fetched shop email shop=%s", shop)
                return str(email).strip()
    except Exception as exc:
        log.warning("shopify_oauth: could not fetch shop email shop=%s: %s", shop, exc)
    return None


# ---------------------------------------------------------------------------
# Merchant upsert
# ---------------------------------------------------------------------------

def _upsert_merchant(db: Session, shop: str, encrypted_token: str, contact_email: Optional[str] = None) -> Merchant:
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is None:
        row = Merchant(
            shop_domain=shop,
            access_token=encrypted_token,
            plan="starter",
            billing_active=False,
            contact_email=contact_email,
            pixel_secret=secrets.token_hex(16),
            gdpr_consent_at=now,
        )
        db.add(row)
        log.info("shopify_oauth: new merchant created shop=%s", shop)
    else:
        row.access_token = encrypted_token
        row.install_status = "active"
        row.uninstalled_at = None
        if contact_email:
            row.contact_email = contact_email
        if not row.pixel_secret:
            row.pixel_secret = secrets.token_hex(16)
        # Re-consent on reinstall
        row.gdpr_consent_at = now
        log.info("shopify_oauth: merchant token refreshed shop=%s plan=%s", shop, row.plan)

    db.commit()
    db.refresh(row)
    return row


def _persist_install_metadata(
    db: Session,
    merchant: Merchant,
    webhook_id: Optional[str],
    script_tag_id: Optional[str],
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = False

    if webhook_id is not None:
        merchant.webhook_id = webhook_id
        merchant.webhook_registered_at = now
        changed = True

    if script_tag_id is not None:
        merchant.script_tag_id = script_tag_id
        merchant.script_tag_installed_at = now
        changed = True

    if changed:
        try:
            db.commit()
        except Exception as exc:
            log.error(
                "shopify_oauth: persist install metadata failed shop=%s: %s",
                merchant.shop_domain,
                exc,
            )
            db.rollback()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/install")
def install(
    request: Request,
    shop: str = QueryParam(..., description="Shopify shop domain"),
    hmac: Optional[str] = QueryParam(None),
    timestamp: Optional[str] = QueryParam(None),
    host: Optional[str] = QueryParam(None),
):
    if not _SHOPIFY_API_KEY:
        return Response("App not configured — contact support.", status_code=500)
    if not is_valid_shop_domain(shop):
        return Response("Invalid shop domain.", status_code=400)
    if not _APP_URL:
        return Response("APP_URL not configured — contact support.", status_code=500)

    if hmac:
        if not _validate_hmac_from_request(request):
            log.warning("shopify_oauth: install HMAC failed shop=%s", shop)
            return Response("Invalid HMAC signature.", status_code=400)

    nonce = secrets.token_hex(32)
    _store_nonce(shop, nonce)

    redirect_uri = f"{_APP_URL}/auth/callback"
    params = urlencode(
        {
            "client_id": _SHOPIFY_API_KEY,
            "scope": _APP_SCOPES,
            "redirect_uri": redirect_uri,
            "state": nonce,
        }
    )
    oauth_url = f"https://{shop}/admin/oauth/authorize?{params}"

    log.info("shopify_oauth: initiating oauth shop=%s nonce=%s…", shop, nonce[:8])
    return RedirectResponse(url=oauth_url, status_code=302)


@router.get("/callback")
async def callback(
    request: Request,
    shop: str = QueryParam(...),
    code: str = QueryParam(...),
    hmac: str = QueryParam(...),
    state: Optional[str] = None,
    timestamp: Optional[str] = None,
    host: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not is_valid_shop_domain(shop):
        log.warning("shopify_oauth: callback rejected — invalid shop: %r", shop)
        return Response("Invalid shop domain.", status_code=400)

    if not _validate_hmac_from_request(request):
        log.warning("shopify_oauth: HMAC failed shop=%s", shop)
        return Response("Invalid HMAC signature.", status_code=400)

    if state and not _consume_nonce(shop, state):
        log.warning("shopify_oauth: state/nonce invalid or expired shop=%s", shop)
        return Response("Invalid or expired state.", status_code=400)

    plaintext_token = await _exchange_code_for_token(shop, code)
    if not plaintext_token:
        log.error("shopify_oauth: token exchange failed shop=%s", shop)
        return Response(
            "Failed to complete installation — could not obtain access token. "
            "Please try again.",
            status_code=502,
        )

    encrypted_token = encrypt_token(plaintext_token)
    contact_email = await _fetch_shop_email(shop, plaintext_token)
    try:
        merchant = _upsert_merchant(db, shop, encrypted_token, contact_email=contact_email)
    except Exception as exc:
        log.error("shopify_oauth: merchant upsert failed shop=%s: %s", shop, exc)
        return Response("Installation failed — please try again.", status_code=500)

    from app.core.merchant_session import set_session_cookie

    sv = getattr(merchant, "session_version", None) or 0

    webhook_ok = False
    webhook_id = None
    try:
        webhook_id, _ = await ensure_orders_webhook(shop, plaintext_token, _APP_URL)
        webhook_ok = webhook_id is not None
        if not webhook_ok:
            log.warning("shopify_oauth: webhook registration failed shop=%s", shop)
    except Exception as exc:
        log.error("shopify_oauth: webhook exception shop=%s: %s", shop, exc)

    tracker_ok = False
    script_tag_id = None
    try:
        script_tag_id, _ = await ensure_tracker_script_tag(
            shop,
            plaintext_token,
            _tracker_url(),
        )
        tracker_ok = script_tag_id is not None
        if not tracker_ok:
            log.warning("shopify_oauth: script tag failed shop=%s", shop)
    except Exception as exc:
        log.error("shopify_oauth: script tag exception shop=%s: %s", shop, exc)

    _persist_install_metadata(db, merchant, webhook_id, script_tag_id)

    log.info(
        "shopify_oauth: install complete shop=%s webhook=%s tracker=%s",
        shop,
        "ok" if webhook_ok else "failed",
        "ok" if tracker_ok else "failed",
    )

    if not _DASHBOARD_URL:
        resp = JSONResponse(
            {
                "status": "installed",
                "shop": shop,
                "webhook": "ok" if webhook_ok else "failed",
                "tracker": "ok" if tracker_ok else "failed",
            }
        )
        set_session_cookie(resp, shop, sv)
        return resp

    dest = (
        f"{_DASHBOARD_URL}/"
        f"?shop={shop}"
        f"&installed=1"
        f"&webhook={'ok' if webhook_ok else 'failed'}"
        f"&tracker={'ok' if tracker_ok else 'failed'}"
    )
    resp = RedirectResponse(url=dest, status_code=302)
    set_session_cookie(resp, shop, sv)
    return resp
