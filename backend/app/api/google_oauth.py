"""
google_oauth.py — G4 Lite parity Google Sheets export API.

Three merchant-facing endpoints:
  GET  /auth/google/start      — redirect merchant to Google consent
  GET  /auth/google/callback   — Google redirects here after consent
  POST /auth/google/disconnect — clear stored refresh_token

One status endpoint:
  GET  /merchant/google/status — { configured, connected, email }

One export endpoint:
  POST /analytics/export-to-sheets — create new sheet + write rows

All Lite-accessible (require_merchant_session) — Better Reports $19.90
/ Report Pundit Free / Mipler $9.99 ship export-to-Sheets at entry.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_merchant_session
from app.models.merchant import Merchant
from app.services.google_sheets import (
    build_authorization_url,
    create_export_sheet,
    disconnect as svc_disconnect,
    exchange_code_for_tokens,
    fetch_userinfo,
    generate_state_token,
    is_configured,
    is_connected,
    store_oauth_tokens,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["google_oauth"])


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class GoogleStatusResponse(BaseModel):
    configured: bool       # True if env vars are set on the backend
    connected: bool        # True if the merchant has a stored refresh_token
    email: str | None = None
    connected_at: str | None = None


@router.get("/merchant/google/status", response_model=GoogleStatusResponse)
def get_google_status(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
):
    merchant = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    return GoogleStatusResponse(
        configured=is_configured(),
        connected=bool(merchant and is_connected(merchant)),
        email=getattr(merchant, "google_oauth_email", None) if merchant else None,
        connected_at=(
            merchant.google_oauth_connected_at.isoformat()
            if merchant and merchant.google_oauth_connected_at else None
        ),
    )


# ---------------------------------------------------------------------------
# OAuth handshake
# ---------------------------------------------------------------------------

# Redis-backed OAuth state storage — required for correctness in
# multi-worker (uvicorn --workers 4) and across backend restarts.
# Key shape: hs:google_oauth_state:{state_token} -> shop_domain
# TTL: 300s (OAuth consent screen completes in seconds; 5min is
# generous for slow readers + tab-switching merchants).
# Promoted from in-memory dict 2026-04-29 after first user flow saw
# state_unknown error (PM2 restart wiped the in-memory map mid-flow).
# multi-worker: redis-backed
_OAUTH_STATE_KEY_PREFIX = "hs:google_oauth_state"
_OAUTH_STATE_TTL_S = 300


def _store_oauth_state(state: str, shop: str) -> bool:
    """Persist state→shop in Redis with TTL. Raises on failure.

    Redis is a hard dependency for HedgeSpark backend. There is no
    in-memory fallback — multi-worker safety + restart-safety both
    require Redis. If Redis is unreachable, surface the error rather
    than silently degrading to a broken in-memory dict.
    """
    from app.core.redis_client import _client
    rc = _client()
    if rc is None:
        raise RuntimeError("oauth_state_redis_unavailable")
    rc.setex(
        f"{_OAUTH_STATE_KEY_PREFIX}:{state}",
        _OAUTH_STATE_TTL_S,
        shop,
    )
    return True


def _consume_oauth_state(state: str) -> str | None:
    """Atomically read+delete state from Redis. Returns shop_domain or
    None if state is unknown / expired / already consumed.

    Uses rc.getdel() (Redis 6.2+) for atomic GET + DEL in one round-trip.
    A naive GET-then-DEL would race: two concurrent callbacks with the
    same state token could both read before either deletes — letting
    a replayed state token authenticate twice.

    Raises if Redis unavailable (consistent with _store_oauth_state).
    """
    from app.core.redis_client import _client
    rc = _client()
    if rc is None:
        raise RuntimeError("oauth_state_redis_unavailable")
    key = f"{_OAUTH_STATE_KEY_PREFIX}:{state}"
    value = rc.getdel(key)
    if value is not None:
        return value.decode() if isinstance(value, bytes) else value
    return None


@router.get("/auth/google/start")
def start_google_oauth(
    shop: str = Depends(require_merchant_session),
):
    """Step 1: redirect the merchant to Google's consent page."""
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Sheets not yet configured by HedgeSpark admin.",
        )
    state = generate_state_token()
    try:
        _store_oauth_state(state, shop)
    except RuntimeError as exc:
        # Redis unavailable. Surface a controlled 503 instead of 500
        # — the merchant sees an error message, not a broken page.
        log.error("oauth_state_store_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="OAuth temporarily unavailable. Try again in a moment.",
        )
    auth_url = build_authorization_url(state)
    # 302 to Google. Browser handles redirect.
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/google/callback")
def google_oauth_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    """Step 2: Google redirects here with `code` + `state`. Exchange,
    store, then redirect the merchant back to the integrations page.

    Note: this endpoint does NOT use require_merchant_session — Google's
    redirect doesn't carry the hs_session cookie reliably (cross-site
    redirect chain). We map state→shop via the in-memory store from step 1.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    # Compute redirect target on the dashboard side regardless of outcome.
    # Path matches the actual settings page at
    # /app/settings/google-sheets/page.tsx — earlier commit shipped the
    # backend with /app/settings/integrations which is a 404 (no such
    # page). Fixed 2026-04-29 same session.
    import os
    dashboard = os.environ.get("DASHBOARD_URL") or "http://127.0.0.1:3000"
    success_url = f"{dashboard}/app/settings/google-sheets?google=connected"
    error_url = f"{dashboard}/app/settings/google-sheets?google=error"

    if error:
        log.warning("google oauth user_denied or error: %s", error)
        return RedirectResponse(url=f"{error_url}&reason={error}", status_code=302)
    if not code or not state:
        return RedirectResponse(url=f"{error_url}&reason=missing_params", status_code=302)

    try:
        shop = _consume_oauth_state(state)
    except RuntimeError as exc:
        log.error("oauth_state_consume_failed: %s", exc)
        return RedirectResponse(url=f"{error_url}&reason=state_unavailable", status_code=302)
    if not shop:
        return RedirectResponse(url=f"{error_url}&reason=state_unknown", status_code=302)

    try:
        tokens = exchange_code_for_tokens(code)
    except ValueError as exc:
        log.warning("google oauth exchange failed for shop=%s: %s", shop, exc)
        return RedirectResponse(url=f"{error_url}&reason=exchange", status_code=302)

    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token") or ""
    if not refresh_token:
        return RedirectResponse(url=f"{error_url}&reason=no_refresh_token", status_code=302)

    # Best-effort email lookup (drive.file scope alone doesn't include
    # email; we requested `openid email` in build_authorization_url
    # implicitly via Google's default behavior, but if /userinfo is
    # missing the connection still works).
    userinfo = fetch_userinfo(access_token) if access_token else {}
    email = userinfo.get("email")

    try:
        store_oauth_tokens(db, shop=shop, refresh_token=refresh_token, email=email)
        db.commit()
    except Exception as exc:
        log.warning("google oauth store failed for shop=%s: %s", shop, exc)
        db.rollback()
        return RedirectResponse(url=f"{error_url}&reason=store", status_code=302)

    return RedirectResponse(url=success_url, status_code=302)


@router.post("/auth/google/disconnect")
def google_disconnect(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Clear the merchant's stored Google OAuth state. Doesn't revoke
    at Google — merchant can do that from their Google account settings."""
    svc_disconnect(db, shop=shop)
    db.commit()
    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Export-to-sheets
# ---------------------------------------------------------------------------


class ExportToSheetsRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    headers: list[str] = Field(..., min_length=1, max_length=50)
    rows: list[list] = Field(..., max_length=50000)  # 50k row hard cap


class ExportToSheetsResponse(BaseModel):
    spreadsheet_id: str
    url: str
    title: str


@router.post("/analytics/export-to-sheets", response_model=ExportToSheetsResponse)
def export_to_sheets(
    payload: ExportToSheetsRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Create a new spreadsheet in the merchant's Drive and write
    the provided headers + rows. Returns the URL the merchant can open
    to view their data.

    Errors:
      503 — not_configured (admin hasn't set GOOGLE_OAUTH_* env)
      409 — not_connected (merchant must complete OAuth flow first)
      502 — Google Sheets API failure
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Sheets export not yet enabled by HedgeSpark admin.",
        )
    try:
        result = create_export_sheet(
            db, shop=shop,
            title=payload.title,
            headers=payload.headers,
            rows=payload.rows,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "not_connected":
            raise HTTPException(
                status_code=409,
                detail="Connect Google Sheets in Settings → Integrations first.",
            )
        log.warning("export_to_sheets failed for shop=%s: %s", shop, msg)
        raise HTTPException(status_code=502, detail=f"google_api_error:{msg}")
    return ExportToSheetsResponse(**result)
