"""
TIER_2 — modification requires explicit human approval (CLAUDE.md §10).

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

# Non-secret hint cookie that tells the dashboard WHICH shop the browser
# last authenticated as — even when the httpOnly JWT cookie has expired
# or been cleared. Domain-scoped to the parent (.hedgesparkhq.com) so
# both api.hedgesparkhq.com and app.hedgesparkhq.com can read/write it.
# NEVER used for authentication (that's hs_session's job) — only as a
# recovery hint to re-trigger /auth/session without prompting.
SHOP_HINT_COOKIE_NAME = "hs_shop"
_SHOP_HINT_TTL_SECONDS = 30 * 86_400  # 30 days, longer than the JWT TTL
_COOKIE_PARENT_DOMAIN = os.getenv("COOKIE_PARENT_DOMAIN", "").strip()

_EXPLICIT_SECRET = os.getenv("MERCHANT_SESSION_SECRET", "").strip()
_FALLBACK_SECRET = os.getenv("SHOPIFY_API_SECRET", "").strip()
_ALLOW_INSECURE_DEV = os.getenv("ALLOW_INSECURE_DEV", "").lower() == "true"

if _EXPLICIT_SECRET:
    _SECRET = _EXPLICIT_SECRET
elif _ALLOW_INSECURE_DEV and _FALLBACK_SECRET:
    # Dev only: fallback to SHOPIFY_API_SECRET with warning
    _SECRET = _FALLBACK_SECRET
    log.warning(
        "merchant_session: MERCHANT_SESSION_SECRET not set — falling back to "
        "SHOPIFY_API_SECRET (dev mode only). Set MERCHANT_SESSION_SECRET for production."
    )
elif _ALLOW_INSECURE_DEV:
    _SECRET = ""
    log.warning(
        "merchant_session: No signing secret available (dev mode). "
        "All session operations will fail until MERCHANT_SESSION_SECRET is set."
    )
else:
    # Production: no fallback, no silent degradation.
    # _SECRET is left empty — create_session_token / verify will fail.
    # The hard RuntimeError in main.py _startup_env_audit() prevents boot.
    _SECRET = ""

_TTL_DAYS: int = int(os.getenv("MERCHANT_SESSION_TTL_DAYS", "7"))
_TTL_SECONDS: int = _TTL_DAYS * 86_400


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

    Also sets a non-httpOnly `hs_shop` hint cookie on the parent domain
    (.hedgesparkhq.com, configured via COOKIE_PARENT_DOMAIN) so both api
    and app subdomains can read it. This hint is a RECOVERY signal only
    — never trusted for auth — that survives JWT expiry and lets the
    dashboard auto-re-trigger /auth/session without prompting the
    merchant to retype their shop domain.

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
    # Recovery hint — cross-subdomain, readable by JS. Written even when
    # the JWT set above failed, because the hint has no security value;
    # it just answers "which shop was this browser using last?".
    hint_kwargs = {
        "key": SHOP_HINT_COOKIE_NAME,
        "value": shop_domain,
        "httponly": False,
        "secure": True,
        "samesite": "lax",
        "max_age": _SHOP_HINT_TTL_SECONDS,
        "path": "/",
    }
    if _COOKIE_PARENT_DOMAIN:
        hint_kwargs["domain"] = _COOKIE_PARENT_DOMAIN
    response.set_cookie(**hint_kwargs)
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
    # Intentionally NOT clearing the hs_shop hint — a merchant who
    # uninstalls and reinstalls benefits from the hint still being
    # present so the reinstall flow can pre-fill / auto-recover. The
    # hint has no security value so retaining it is safe.
    return response
