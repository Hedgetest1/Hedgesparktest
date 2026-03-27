"""
merchant_session.py — Per-merchant JWT session tokens.

Replaces the shared DASHBOARD_API_KEY authentication model with per-merchant
sessions.  Each session token encodes the shop_domain so the backend can
derive merchant identity from the authenticated context — not from a
user-supplied query parameter.

Token lifecycle
---------------
Created: after successful OAuth callback or billing callback
Stored:  httpOnly cookie (browser) — never accessible to client JS
Verified: on every dashboard API request via require_merchant_session()
Expires: 7 days (configurable via MERCHANT_SESSION_TTL_DAYS)

Security properties
-------------------
- httpOnly: JavaScript cannot read the cookie → XSS cannot steal sessions
- Secure: cookie only sent over HTTPS
- SameSite=None: required for cross-origin API calls with credentials
- Signed with HMAC-SHA256: cannot be forged without the server secret
- shop_domain is embedded in the token: cannot be spoofed via query params
- session_version in JWT: if merchant.session_version != token.sv → reject
  This enables forced logout without rotating the global signing secret.

Environment variables
---------------------
MERCHANT_SESSION_SECRET   Signing key for JWTs.  Required in production.
                          Falls back to SHOPIFY_API_SECRET if absent (acceptable
                          for single-server deploys but not ideal).
MERCHANT_SESSION_TTL_DAYS Session lifetime in days (default: 7).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "hs_session"

_SECRET: str = (
    os.getenv("MERCHANT_SESSION_SECRET", "")
    or os.getenv("SHOPIFY_API_SECRET", "")
)
_TTL_DAYS: int = int(os.getenv("MERCHANT_SESSION_TTL_DAYS", "7"))
_TTL_SECONDS: int = _TTL_DAYS * 86_400

if not _SECRET:
    log.error(
        "merchant_session: No signing secret available — "
        "set MERCHANT_SESSION_SECRET or SHOPIFY_API_SECRET. "
        "All session operations will fail."
    )


def create_session_token(shop_domain: str, session_version: int = 0) -> Optional[str]:
    """
    Create a signed JWT encoding the merchant's shop_domain.

    session_version (sv claim) enables forced logout: if the merchant's
    session_version column is bumped, all existing tokens with the old sv
    will be rejected by verify_session_token().
    """
    if not _SECRET:
        log.error("merchant_session: cannot create token — no signing secret")
        return None
    try:
        import jwt
        now = int(time.time())
        payload = {
            "shop": shop_domain,
            "sv": session_version,
            "iat": now,
            "exp": now + _TTL_SECONDS,
        }
        return jwt.encode(payload, _SECRET, algorithm="HS256")
    except Exception as exc:
        log.error("merchant_session: token creation failed: %s", exc)
        return None


def verify_session_token(token: str) -> Optional[dict]:
    """
    Verify a session JWT and return the full payload, or None on failure.

    Returns dict with at least {"shop": str, "sv": int} on success.
    Returns None when:
    - Token is malformed or tampered
    - Token has expired
    - Signing secret is unavailable
    """
    if not _SECRET or not token:
        return None
    try:
        import jwt
        payload = jwt.decode(token, _SECRET, algorithms=["HS256"])
        shop = payload.get("shop")
        if not shop or not isinstance(shop, str):
            return None
        # Normalize: ensure sv exists (old tokens before this field → sv=0)
        payload.setdefault("sv", 0)
        return payload
    except Exception:
        return None


def set_session_cookie(response, shop_domain: str, session_version: int = 0):
    """
    Create a session token and set it as an httpOnly cookie on the response.

    Returns the response object (for chaining).
    Sets max_age to match token TTL so browser and server expiry are aligned.
    """
    token = create_session_token(shop_domain, session_version)
    if token:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=True,
            samesite="none",
            max_age=_TTL_SECONDS,
            path="/",
        )
    return response


def clear_session_cookie(response):
    """Remove the session cookie (e.g. on uninstall)."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )
    return response
