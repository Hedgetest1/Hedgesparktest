"""
shopify_oauth.py — Shopify OAuth 2.0 install flow.

Endpoints
---------
GET /auth/install?shop=<myshopify-domain>
    Entry point.  Generates a nonce, stores it, then redirects the browser
    to Shopify's OAuth permission screen.

GET /auth/callback?shop=&code=&hmac=&state=&timestamp=
    Full post-OAuth install pipeline:
      1. Validate shop domain format
      2. Validate HMAC signature (prevents request tampering)
      3. Verify + consume nonce (prevents CSRF)
      4. Exchange code for access token (Shopify OAuth)
      5. Encrypt token at rest (AES-256-GCM via token_crypto)
      6. Upsert merchant record
      7. Register orders/paid webhook (idempotent, non-blocking)
      8. Inject spark-tracker.js script tag (idempotent, non-blocking)
      9. Persist webhook_id + script_tag_id to merchant row
     10. Redirect to dashboard with install status flags

Install status flags in redirect URL
-------------------------------------
DASHBOARD_URL/?shop=<domain>&installed=1&webhook=ok&tracker=ok
DASHBOARD_URL/?shop=<domain>&installed=1&webhook=failed&tracker=ok

These flags are honest — the dashboard can display appropriate setup
guidance when webhook or tracker registration fails at install time.
A "failed" flag means the merchant may need to re-trigger or the
operator needs to investigate.  The merchant IS installed (token stored);
only the auto-setup steps partially failed.

Security model
--------------
Token encryption:
    Shopify access tokens are encrypted with AES-256-GCM before storage.
    Requires MERCHANT_TOKEN_ENCRYPTION_KEY env var (32 bytes, hex or base64).
    Degrades gracefully to plaintext with a WARNING when key is absent.
    See app/core/token_crypto.py for encryption details.

HMAC validation:
    All callback parameters (except hmac itself) are sorted alphabetically,
    joined as key=value pairs with "&", then HMAC-SHA256'd with SHOPIFY_API_SECRET.
    The computed digest is compared using hmac.compare_digest().

State nonce:
    32-byte cryptographically random hex nonce stored in Redis (TTL 300s)
    with in-memory dict fallback.  Consumed atomically on callback.

Token storage:
    Token is encrypted with encrypt_token() before writing to DB.
    Existing plaintext tokens are transparently readable via decrypt_token().

Scopes:
    SHOPIFY_APP_SCOPES env var
    Default: "read_products,read_orders,write_script_tags"

Environment variables
---------------------
Required:
    SHOPIFY_API_KEY              — App client ID
    SHOPIFY_API_SECRET           — App client secret
    APP_URL                      — Backend base URL (e.g. https://api.hedgesparkhq.com)
    DASHBOARD_URL                — Frontend URL  (e.g. https://app.hedgesparkhq.com)

Strongly recommended:
    MERCHANT_TOKEN_ENCRYPTION_KEY — 32-byte hex key for token encryption
    REDIS_URL                     — Nonce store; required for multi-process deploy

Optional:
    SHOPIFY_APP_SCOPES           — Override OAuth scope string
    TRACKER_SCRIPT_URL           — Override tracker URL injected as Script Tag
                                   Default: {APP_URL}/tracker.js

Shopify Partner Dashboard: register {APP_URL}/auth/callback as the
allowed redirection URL before testing the install flow.
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

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.token_crypto import decrypt_token, encrypt_token
from app.models.merchant import Merchant
from app.services.shopify_admin import ensure_orders_webhook, ensure_tracker_script_tag
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["shopify-oauth"])

# ---------------------------------------------------------------------------
# Configuration — read at import time; restart required after change
# ---------------------------------------------------------------------------

_SHOPIFY_API_KEY:    str = os.getenv("SHOPIFY_API_KEY",    "")
_SHOPIFY_API_SECRET: str = os.getenv("SHOPIFY_API_SECRET", "")
_APP_URL:            str = os.getenv("APP_URL",            "").rstrip("/")
_DASHBOARD_URL:      str = os.getenv("DASHBOARD_URL",      "").rstrip("/")
_APP_SCOPES:         str = os.getenv("SHOPIFY_APP_SCOPES",
                                     "read_products,read_orders,write_script_tags")

def _tracker_url() -> str:
    """Return the tracker script URL to register as a Shopify Script Tag."""
    override = os.getenv("TRACKER_SCRIPT_URL", "").strip()
    return override if override else f"{_APP_URL}/tracker.js"


# Nonce TTL — must exceed worst-case OAuth browser round-trip
NONCE_TTL_SECS: int = 300

# ---------------------------------------------------------------------------
# Nonce store — Redis primary, in-memory fallback
# ---------------------------------------------------------------------------

_mem_nonces: dict[str, float] = {}
_mem_nonces_lock: Lock = Lock()


def _nonce_key(shop: str, nonce: str) -> str:
    return f"hs:oauth_nonce:{shop}:{nonce}"


def _store_nonce(shop: str, nonce: str) -> None:
    key = _nonce_key(shop, nonce)
    try:
        from app.core.redis_client import _client as redis_client
        client = redis_client()
        if client is not None:
            client.setex(key, NONCE_TTL_SECS, "1")
            return
    except Exception as exc:
        log.warning("shopify_oauth: Redis nonce store failed: %s — using in-memory", exc)

    with _mem_nonces_lock:
        _mem_nonces[key] = time.monotonic() + NONCE_TTL_SECS
        now = time.monotonic()
        expired = [k for k, exp in _mem_nonces.items() if exp < now]
        for k in expired:
            del _mem_nonces[k]


def _consume_nonce(shop: str, nonce: str) -> bool:
    key = _nonce_key(shop, nonce)
    try:
        from app.core.redis_client import _client as redis_client
        client = redis_client()
        if client is not None:
            deleted = client.delete(key)
            return deleted > 0
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

def _validate_hmac(params: dict, provided_hmac: str) -> bool:
    if not _SHOPIFY_API_SECRET:
        log.error("shopify_oauth: SHOPIFY_API_SECRET not configured — HMAC impossible")
        return False
    filtered = {k: v for k, v in params.items() if k != "hmac"}
    message  = "&".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    digest   = _hmac_module.new(
        _SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _hmac_module.compare_digest(digest, provided_hmac)


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

async def _exchange_code_for_token(shop: str, code: str) -> Optional[str]:
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
        data  = resp.json()
        token = data.get("access_token")
        scope = data.get("scope", "")
        if token:
            log.info("shopify_oauth: token exchange OK shop=%s scope=%s", shop, scope)
        return token or None
    except Exception as exc:
        log.error("shopify_oauth: token exchange exception shop=%s: %s", shop, exc)
        return None


# ---------------------------------------------------------------------------
# Merchant upsert
# ---------------------------------------------------------------------------

def _upsert_merchant(db: Session, shop: str, encrypted_token: str) -> Merchant:
    """
    Create a new merchant row or refresh the token on reinstall.

    Accepts the already-encrypted token — encryption happens in the caller
    before this function is invoked.

    On reinstall: only access_token is updated.  plan and billing_active are
    preserved so Pro subscribers do not lose their tier on reinstall.
    """
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if row is None:
        row = Merchant(
            shop_domain    = shop,
            access_token   = encrypted_token,
            plan           = "starter",
            billing_active = False,
        )
        db.add(row)
        log.info("shopify_oauth: new merchant created shop=%s", shop)
    else:
        row.access_token    = encrypted_token
        # Reinstall — restore active status in case merchant had previously uninstalled
        row.install_status  = "active"
        row.uninstalled_at  = None
        log.info("shopify_oauth: merchant token refreshed shop=%s plan=%s", shop, row.plan)
    db.commit()
    db.refresh(row)
    return row


def _persist_install_metadata(
    db:                  Session,
    merchant:            Merchant,
    webhook_id:          Optional[str],
    script_tag_id:       Optional[str],
) -> None:
    """
    Write webhook_id and script_tag_id back to the merchant row.
    Called after async registration steps complete.
    Only updates columns that received a value — failed steps leave their
    existing column value unchanged (None on first install).
    """
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    changed   = False
    if webhook_id is not None:
        merchant.webhook_id            = webhook_id
        merchant.webhook_registered_at = now_naive
        changed = True
    if script_tag_id is not None:
        merchant.script_tag_id          = script_tag_id
        merchant.script_tag_installed_at = now_naive
        changed = True
    if changed:
        try:
            db.commit()
        except Exception as exc:
            log.error(
                "shopify_oauth: failed to persist install metadata shop=%s: %s",
                merchant.shop_domain, exc,
            )
            db.rollback()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/install")
def install(shop: str, response: Response):
    """
    OAuth install entry point.

    Validates shop domain, generates nonce, redirects to Shopify permission screen.
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
    Shopify OAuth callback — full install pipeline.

    Steps 1-6 are blocking (install fails on any error).
    Steps 7-8 are non-blocking (install succeeds even if these fail).

    The post-install redirect URL includes honest status flags:
        ?installed=1&webhook=ok|failed&tracker=ok|failed

    The dashboard must display appropriate guidance when webhook=failed
    or tracker=failed (e.g. "Revenue data requires a manual webhook setup").
    """
    # Step 1 — validate shop domain
    if not is_valid_shop_domain(shop):
        log.warning("shopify_oauth: callback rejected — invalid shop domain: %r", shop)
        return Response("Invalid shop domain.", status_code=400)

    # Step 2 — HMAC validation
    callback_params: dict = {"shop": shop, "code": code, "state": state}
    if timestamp:
        callback_params["timestamp"] = timestamp

    if not _validate_hmac(callback_params, hmac):
        log.warning(
            "shopify_oauth: HMAC validation failed shop=%s — "
            "possible replay or CSRF attempt",
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

    # Step 4 — exchange code for access token
    plaintext_token = await _exchange_code_for_token(shop, code)
    if not plaintext_token:
        log.error("shopify_oauth: token exchange failed for shop=%s", shop)
        return Response(
            "Failed to complete installation — could not obtain access token. "
            "Please try again.",
            status_code=502,
        )

    # Step 5 — encrypt token
    encrypted_token = encrypt_token(plaintext_token)

    # Step 6 — upsert merchant with encrypted token
    try:
        merchant = _upsert_merchant(db, shop, encrypted_token)
    except Exception as exc:
        log.error("shopify_oauth: merchant upsert failed shop=%s: %s", shop, exc)
        return Response(
            "Installation failed — could not save merchant record. "
            "Please try again or contact support.",
            status_code=500,
        )

    # Steps 7-8 — non-blocking post-install setup
    # Use the plaintext token for API calls (never store it, only the encrypted form)
    webhook_ok    = False
    tracker_ok    = False
    webhook_id    = None
    script_tag_id = None

    # Step 7 — register orders/paid webhook
    try:
        webhook_id, _ = await ensure_orders_webhook(shop, plaintext_token, _APP_URL)
        webhook_ok = webhook_id is not None
        if not webhook_ok:
            log.warning(
                "shopify_oauth: webhook registration failed shop=%s — "
                "revenue intelligence will not populate automatically. "
                "Manual setup required: POST %s/webhooks/shopify/orders-paid",
                shop, _APP_URL,
            )
    except Exception as exc:
        log.error("shopify_oauth: webhook registration exception shop=%s: %s", shop, exc)

    # Step 8 — inject tracker script tag
    try:
        t_url = _tracker_url()
        script_tag_id, _ = await ensure_tracker_script_tag(shop, plaintext_token, t_url)
        tracker_ok = script_tag_id is not None
        if not tracker_ok:
            log.warning(
                "shopify_oauth: script tag installation failed shop=%s — "
                "behavioral tracking requires manual <script> tag installation. "
                "Add to theme: <script async src=\"%s\"></script>",
                shop, _tracker_url(),
            )
    except Exception as exc:
        log.error("shopify_oauth: script tag exception shop=%s: %s", shop, exc)

    # Step 9 — persist install metadata (webhook_id / script_tag_id)
    _persist_install_metadata(db, merchant, webhook_id, script_tag_id)

    # Step 10 — redirect to dashboard with honest status flags
    log.info(
        "shopify_oauth: install complete shop=%s webhook=%s tracker=%s",
        shop,
        "ok" if webhook_ok else "failed",
        "ok" if tracker_ok else "failed",
    )

    if not _DASHBOARD_URL:
        log.warning("shopify_oauth: DASHBOARD_URL not configured")
        return {
            "status":   "installed",
            "shop":     shop,
            "webhook":  "ok" if webhook_ok else "failed",
            "tracker":  "ok" if tracker_ok else "failed",
        }

    dest = (
        f"{_DASHBOARD_URL}/"
        f"?shop={shop}"
        f"&installed=1"
        f"&webhook={'ok' if webhook_ok else 'failed'}"
        f"&tracker={'ok' if tracker_ok else 'failed'}"
    )
    return RedirectResponse(url=dest, status_code=302)
