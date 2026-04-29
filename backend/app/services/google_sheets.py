"""
google_sheets.py — G4 Lite parity Google Sheets export service.

OAuth scope: `auth/drive.file` (NON-SENSITIVE, no Google verification
required). Pattern: app creates a NEW spreadsheet in the merchant's
Drive each time they click "Export to Sheets". Merchant owns the
sheet; HedgeSpark only retains access to sheets it created.

Implementation: httpx-only, no `google-api-python-client` dependency
to keep the bundle slim. Uses Google's standard REST endpoints for
OAuth + Drive + Sheets API v4.

Environment dependencies:
  - GOOGLE_OAUTH_CLIENT_ID
  - GOOGLE_OAUTH_CLIENT_SECRET
  - APP_URL (existing — used to build redirect_uri)

When env vars are missing, `is_configured()` returns False and the
upstream API surfaces a "config-needed" state to the merchant —
nothing crashes, nothing 500s.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.token_crypto import decrypt_token, encrypt_token
from app.models.merchant import Merchant

log = logging.getLogger(__name__)

# Google OAuth + API endpoints.
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
# drive.file = NON-SENSITIVE (only files this app creates).
# openid + email are also NON-SENSITIVE — needed so /userinfo returns
# the merchant's Google email for display ("Connected as user@brand.com").
# Without these, fetch_userinfo returns empty + we can't show whose
# account is authorized.
_OAUTH_SCOPE = "openid email https://www.googleapis.com/auth/drive.file"

# In-memory access-token cache: shop_domain -> (token, expires_at_unix).
# Refresh token is encrypted in DB; access token is short-lived (~1h) so
# we cache to avoid hitting Google's token endpoint on every export.
# multi-worker: accept-degrade — each uvicorn worker keeps its own cache.
# Worst case: 4× the refresh API calls (still well within Google's free
# 60 refreshes/min/user limit). Cache misses transparently re-refresh
# from the encrypted refresh_token in DB.
_access_token_cache: dict[str, tuple[str, float]] = {}
_ACCESS_TOKEN_LEEWAY_S = 60  # refresh 60s before nominal expiry


def is_configured() -> bool:
    """True iff GOOGLE_OAUTH_CLIENT_ID + _CLIENT_SECRET env vars are set.

    Used by the API layer to surface a "config-needed" state to the
    merchant when the founder hasn't yet provisioned Google Cloud
    Console credentials. Nothing in this module crashes when these
    are absent — we just refuse to start the OAuth flow.
    """
    return bool(_get_client_id()) and bool(_get_client_secret())


def _get_client_id() -> str | None:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or None


def _get_client_secret() -> str | None:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or None


def _redirect_uri() -> str:
    """Build the OAuth callback URL from APP_URL (env)."""
    base = (os.environ.get("APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/auth/google/callback"


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


def build_authorization_url(state: str) -> str:
    """Step 1 of OAuth: build the URL the merchant gets redirected to.

    `state` is a random token we generate per-flow, validated on
    callback to prevent CSRF. Caller is responsible for storing it
    against the merchant's session.
    """
    params = {
        "client_id": _get_client_id() or "",
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": _OAUTH_SCOPE,
        "state": state,
        "access_type": "offline",  # required to get refresh_token
        "prompt": "consent",       # force consent so refresh_token is issued
        "include_granted_scopes": "true",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def generate_state_token() -> str:
    """CSRF-protection token for OAuth. 32 bytes urlsafe."""
    return secrets.token_urlsafe(32)


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Step 3 of OAuth: exchange the authorization code for tokens.

    Returns the JSON response from Google: refresh_token + access_token
    + expires_in + id_token (for email lookup) + scope.

    Raises ValueError on any 4xx/5xx response.
    """
    if not is_configured():
        raise ValueError("google_oauth_not_configured")
    payload = {
        "code": code,
        "client_id": _get_client_id(),
        "client_secret": _get_client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(_TOKEN_URL, data=payload)
    if resp.status_code != 200:
        log.warning("google oauth exchange failed status=%s body=%s",
                    resp.status_code, resp.text[:200])
        raise ValueError(f"google_oauth_exchange_failed:{resp.status_code}")
    data = resp.json()
    if "refresh_token" not in data:
        # Google only issues refresh_token on first consent (or with
        # prompt=consent). If we don't get one, the flow is unusable.
        raise ValueError("google_oauth_no_refresh_token")
    return data


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """Step 3.5: fetch the merchant's Google account email for display.

    drive.file doesn't include email — we hit /userinfo separately.
    The OAuth flow MUST request `openid email` to get this; falls
    back to id_token decode if response is empty.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(_USERINFO_URL, headers=headers)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        log.warning("google userinfo lookup failed: %s", exc)
    return {}


def store_oauth_tokens(
    db: Session,
    *,
    shop: str,
    refresh_token: str,
    email: str | None,
) -> None:
    """Persist the encrypted refresh_token + display email + connected_at."""
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if merchant is None:
        raise ValueError("merchant_not_found")
    merchant.encrypted_google_refresh_token = encrypt_token(refresh_token)
    merchant.google_oauth_email = (email or "")[:255] or None
    merchant.google_oauth_connected_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # Bust in-memory cache for this shop so the next call picks up the
    # new refresh token via the standard refresh flow.
    _access_token_cache.pop(shop, None)


def disconnect(db: Session, *, shop: str) -> None:
    """Clear stored OAuth state for the merchant. Doesn't revoke at
    Google (merchant can do that from their Google account settings)."""
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if merchant is None:
        return
    merchant.encrypted_google_refresh_token = None
    merchant.google_oauth_email = None
    merchant.google_oauth_connected_at = None
    _access_token_cache.pop(shop, None)


def is_connected(merchant: Merchant) -> bool:
    return bool(merchant.encrypted_google_refresh_token)


# ---------------------------------------------------------------------------
# Access token refresh (transparent, in-memory cached)
# ---------------------------------------------------------------------------


def _refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Trade the long-lived refresh_token for a short-lived access_token."""
    if not is_configured():
        raise ValueError("google_oauth_not_configured")
    payload = {
        "refresh_token": refresh_token,
        "client_id": _get_client_id(),
        "client_secret": _get_client_secret(),
        "grant_type": "refresh_token",
    }
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(_TOKEN_URL, data=payload)
    if resp.status_code != 200:
        log.warning("google oauth refresh failed status=%s body=%s",
                    resp.status_code, resp.text[:200])
        raise ValueError(f"google_oauth_refresh_failed:{resp.status_code}")
    return resp.json()


def get_access_token(db: Session, *, shop: str) -> str:
    """Return a valid access_token for the given shop.

    Uses the in-memory cache when possible; refreshes via the stored
    refresh_token when the cached token is missing or expired.

    Raises ValueError("not_connected") if the merchant has no stored
    refresh_token. Raises ValueError("google_oauth_*") on Google API
    failures.
    """
    cached = _access_token_cache.get(shop)
    if cached and cached[1] - _ACCESS_TOKEN_LEEWAY_S > time.time():
        return cached[0]
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    if merchant is None or not merchant.encrypted_google_refresh_token:
        raise ValueError("not_connected")
    refresh_token = decrypt_token(merchant.encrypted_google_refresh_token)
    if not refresh_token:
        raise ValueError("not_connected")
    fresh = _refresh_access_token(refresh_token)
    access_token = fresh.get("access_token")
    expires_in = int(fresh.get("expires_in", 3600))
    if not access_token:
        raise ValueError("google_oauth_no_access_token")
    _access_token_cache[shop] = (access_token, time.time() + expires_in)
    return access_token


# ---------------------------------------------------------------------------
# Sheet creation + write
# ---------------------------------------------------------------------------


def create_export_sheet(
    db: Session,
    *,
    shop: str,
    title: str,
    headers: list[str],
    rows: list[list[Any]],
) -> dict[str, str]:
    """Create a new spreadsheet in the merchant's Drive and write the
    headers + rows. Returns {"spreadsheet_id": str, "url": str, "title": str}.

    `rows` is a list-of-lists where each inner list aligns with `headers`
    by position. Cell values: strings render as text, numbers as numbers,
    booleans as TRUE/FALSE. Dates should be pre-formatted as ISO strings.

    The merchant becomes the file owner immediately (drive.file scope
    creates files in the merchant's Drive). HedgeSpark retains the
    ability to update/delete this specific file via drive.file scope
    until the merchant manually removes our access.
    """
    access_token = get_access_token(db, shop=shop)
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # 1. Create spreadsheet with title.
    safe_title = title[:120] or "HedgeSpark Export"
    create_body = {
        "properties": {"title": safe_title},
        "sheets": [{"properties": {"title": "Sheet1"}}],
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_SHEETS_API, headers=auth_headers, json=create_body)
    if resp.status_code != 200:
        log.warning("sheets create failed status=%s body=%s",
                    resp.status_code, resp.text[:200])
        raise ValueError(f"sheets_create_failed:{resp.status_code}")
    created = resp.json()
    spreadsheet_id = created["spreadsheetId"]

    # 2. Append headers + rows in a single batch.
    values = [headers] + [list(r) for r in rows]
    append_url = (
        f"{_SHEETS_API}/{spreadsheet_id}/values/Sheet1!A1:append"
        "?valueInputOption=USER_ENTERED&insertDataOption=OVERWRITE"
    )
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            append_url,
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"range": "Sheet1!A1", "majorDimension": "ROWS", "values": values},
        )
    if resp.status_code not in (200, 201):
        log.warning("sheets append failed status=%s body=%s",
                    resp.status_code, resp.text[:200])
        raise ValueError(f"sheets_append_failed:{resp.status_code}")

    return {
        "spreadsheet_id": spreadsheet_id,
        "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
        "title": safe_title,
    }
