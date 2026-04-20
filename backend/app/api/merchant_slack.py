"""
merchant_slack.py — Slack integration endpoints for the merchant.

Strada 3.5 + 4 dominance (2026-04-20). Six endpoints now:
  GET  /merchant/slack/status            — connected / error state
  POST /merchant/slack/connect           — save pasted webhook URL
  POST /merchant/slack/test              — re-send test message
  DELETE /merchant/slack                 — disconnect
  GET  /merchant/slack/oauth/authorize   — Strada 4: start OAuth flow
  GET  /merchant/slack/oauth/callback    — Strada 4: Slack returns here

One-click OAuth (Strada 4): the merchant clicks "Connect Slack", we
redirect to slack.com/oauth/v2/authorize with scope=incoming-webhook,
merchant picks a channel in Slack's UI, Slack redirects back to our
callback with a short-lived `code` and our `state`. We verify state
(CSRF guard), exchange code for a webhook URL at slack.com/api/oauth.
v2.access, encrypt + store the URL via the existing slack_dispatcher.
save_webhook path. Zero manual webhook creation on the merchant's side.

The `/connect` paste-URL endpoint stays alive as manual fallback for
merchants who prefer not to use OAuth (or for early-beta merchants
without proper Slack permissions in their workspace).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services import slack_dispatcher

log = logging.getLogger(__name__)

router = APIRouter(prefix="/merchant/slack", tags=["merchant_slack"])


class SlackStatusResponse(BaseModel):
    connected: bool
    status: str
    last_error: str | None = None


class SlackConnectRequest(BaseModel):
    webhook_url: str = Field(..., max_length=500)


class SlackConnectResponse(BaseModel):
    ok: bool
    status: str
    error: str | None = None


@router.get("/status", response_model=SlackStatusResponse)
def get_slack_status(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Return the merchant's Slack integration state (never the URL
    itself)."""
    return slack_dispatcher.get_status(db, shop)


@router.post("/connect", response_model=SlackConnectResponse)
def connect_slack(
    body: SlackConnectRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Save the webhook URL + send a confirmation message. If the
    confirmation post fails, the webhook is still saved but status is
    set to 'error' with the sanitized reason in last_error."""
    ok, err = slack_dispatcher.save_webhook(db, shop, body.webhook_url)
    if not ok:
        raise HTTPException(status_code=400, detail=err)

    # Immediate test post confirms the URL works before the merchant
    # expects their daily brief to arrive.
    ok, err = slack_dispatcher.post_message(
        db,
        shop,
        ":spark: *HedgeSpark connected to this channel.*\nYour morning brief will arrive here daily around 08:00 Europe/Rome.",
    )
    status = slack_dispatcher.get_status(db, shop)
    return SlackConnectResponse(
        ok=ok,
        status=status["status"],
        error=err if not ok else None,
    )


@router.post("/test", response_model=SlackConnectResponse)
def test_slack(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Re-send a test message on an already-saved webhook. Useful to
    verify a channel is still receiving after a slack config change."""
    ok, err = slack_dispatcher.post_message(
        db,
        shop,
        ":test_tube: *HedgeSpark test message.*\nIf you see this, your Slack integration is working.",
    )
    status = slack_dispatcher.get_status(db, shop)
    return SlackConnectResponse(
        ok=ok,
        status=status["status"],
        error=err if not ok else None,
    )


@router.delete("", response_model=SlackStatusResponse)
def disconnect_slack(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Remove the Slack webhook. Idempotent."""
    slack_dispatcher.disconnect(db, shop)
    return slack_dispatcher.get_status(db, shop)


# ---------------------------------------------------------------------------
# Strada 4 — one-click OAuth flow
# ---------------------------------------------------------------------------
#
# Slack OAuth v2 (scope=incoming-webhook) — merchant picks a channel in
# Slack's own UI, Slack returns us a ready-to-use webhook URL. Zero manual
# webhook creation on the merchant's side (the previous paste-URL flow
# required them to create it themselves in Slack settings first).
#
# Auth: /oauth/authorize requires merchant session (we need to know which
# shop the OAuth is for). /oauth/callback does NOT require a session
# cookie — it's called by Slack, not the merchant's browser via our auth
# flow. Instead, we pack shop_domain + a random nonce into the `state`
# parameter and HMAC-sign it with SLACK_SIGNING_SECRET so an attacker
# can't forge a callback for someone else's shop.
#
# State TTL: 10 minutes. Merchants who start the flow and abandon it
# can't have a zombie state sitting around for hours.


_STATE_TTL_S = 600  # 10 minutes


def _slack_client_id() -> str:
    return os.environ.get("SLACK_CLIENT_ID", "").strip()


def _slack_client_secret() -> str:
    return os.environ.get("SLACK_CLIENT_SECRET", "").strip()


def _slack_signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "").strip()


def _slack_redirect_uri() -> str:
    return os.environ.get(
        "SLACK_OAUTH_REDIRECT_URI",
        "https://api.hedgesparkhq.com/merchant/slack/oauth/callback",
    ).strip()


def _dashboard_return_url() -> str:
    """Where to bounce the merchant's browser after callback completes
    (success or failure). Reads APP_URL so dev and prod both land on
    the right dashboard host."""
    base = os.environ.get("APP_URL", "https://app.hedgesparkhq.com").rstrip("/")
    return f"{base}/app/lite"


def _sign_state(payload: dict) -> str:
    """HMAC-signed state token — prevents an attacker from calling our
    callback with a forged shop_domain. Format: base64(json).base64(sig)."""
    import base64
    body = json.dumps(payload, separators=(",", ":")).encode()
    body_b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
    key = _slack_signing_secret().encode()
    if not key:
        # Degraded-but-safe: if signing secret is missing, use a random
        # nonce so at least the state is unguessable for the session.
        key = secrets.token_bytes(32)
    sig = hmac.new(key, body_b64.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body_b64}.{sig}"


def _verify_state(state: str) -> dict | None:
    """Reverse of _sign_state. Returns the decoded payload dict on
    success, None on any tamper / signature mismatch / expiry."""
    import base64
    if not state or state.count(".") != 1:
        return None
    body_b64, sig = state.split(".", 1)
    key = _slack_signing_secret().encode()
    if not key:
        return None  # can't verify without secret
    expected = hmac.new(key, body_b64.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        padding = "=" * (-len(body_b64) % 4)
        body = base64.urlsafe_b64decode(body_b64 + padding)
        payload = json.loads(body)
    except Exception:
        return None
    # Expiry
    issued = payload.get("ts", 0)
    if not isinstance(issued, (int, float)) or time.time() - issued > _STATE_TTL_S:
        return None
    return payload


@router.get("/oauth/authorize", include_in_schema=False)
def oauth_authorize(
    shop: str = Depends(require_merchant_session),
):
    """Redirect the merchant to Slack's OAuth authorization page with
    scope=incoming-webhook. Slack will ask them to pick a channel; on
    approval it redirects back to our /oauth/callback with a one-shot
    code that we exchange for the webhook URL."""
    client_id = _slack_client_id()
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Slack OAuth not configured on this deployment",
        )

    state = _sign_state({
        "shop": shop,
        "nonce": secrets.token_urlsafe(16),
        "ts": int(time.time()),
    })

    params = {
        "client_id": client_id,
        "scope": "incoming-webhook",
        "redirect_uri": _slack_redirect_uri(),
        "state": state,
    }
    url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return RedirectResponse(url, status_code=302)


def _render_callback_result(title: str, message: str, ok: bool) -> HTMLResponse:
    """Tiny inline HTML page that auto-closes the OAuth popup (or
    redirects the main window if the merchant opened the authorize URL
    in the same tab). Status color + autoclose script so the UX is
    "click Connect → Slack screen → back here with a green tick"."""
    color = "#10b981" if ok else "#f87171"
    return_url = _dashboard_return_url()
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ margin:0; background:#07070f; color:#e2e8f0; font-family:system-ui,-apple-system,sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; }}
.card {{ max-width:420px; padding:40px 32px; border-radius:20px; border:1px solid rgba(255,255,255,0.08); background:#0e0e1a; text-align:center; }}
.badge {{ display:inline-block; width:56px; height:56px; border-radius:50%; margin-bottom:20px; font-size:32px; line-height:56px; background:{color}22; color:{color}; }}
h1 {{ margin:0 0 12px 0; font-size:20px; font-weight:700; letter-spacing:-0.2px; }}
p {{ margin:0 0 24px 0; font-size:14px; line-height:1.6; color:#94a3b8; }}
.btn {{ display:inline-block; padding:10px 20px; border-radius:10px; background:linear-gradient(135deg,#7c3aed,#c026d3); color:#fff; text-decoration:none; font-size:13px; font-weight:700; }}
</style>
</head><body>
<div class="card">
  <div class="badge">{"✓" if ok else "✗"}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  <a href="{return_url}" class="btn">Back to dashboard</a>
</div>
<script>
// Auto-close the popup if opener exists; otherwise auto-redirect after 2s
if (window.opener && !window.opener.closed) {{
  try {{ window.opener.postMessage({{type: "hs-slack-oauth", ok: {str(ok).lower()}}}, "*"); }} catch (e) {{}}
  setTimeout(() => window.close(), 800);
}} else {{
  setTimeout(() => {{ window.location.href = "{return_url}"; }}, 2000);
}}
</script>
</body></html>"""
    return HTMLResponse(html, status_code=200)


@router.get("/oauth/callback", include_in_schema=False)
def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Slack redirects here after the merchant authorizes. We verify
    the state (CSRF), exchange the code for a webhook URL at
    slack.com/api/oauth.v2.access, and save via slack_dispatcher.
    Never trusts the session cookie (this is called by Slack's
    redirect, not by the merchant's logged-in tab) — all shop identity
    comes from the signed state."""
    # Slack-reported error (merchant denied, etc.)
    if error:
        return _render_callback_result(
            "Slack connect cancelled",
            f"The Slack authorization was cancelled or failed ({error}). Nothing was saved.",
            ok=False,
        )

    if not code or not state:
        return _render_callback_result(
            "Invalid Slack callback",
            "The callback from Slack was missing required parameters. Try again from the dashboard.",
            ok=False,
        )

    payload = _verify_state(state)
    if not payload:
        log.warning("merchant_slack: oauth callback with invalid/expired state")
        return _render_callback_result(
            "Session expired",
            "The OAuth session expired or was tampered. Restart the connection from the dashboard.",
            ok=False,
        )
    shop = payload.get("shop") or ""
    if not shop:
        return _render_callback_result(
            "Invalid session",
            "We couldn't identify your shop from the OAuth state. Restart from the dashboard.",
            ok=False,
        )

    client_id = _slack_client_id()
    client_secret = _slack_client_secret()
    if not client_id or not client_secret:
        return _render_callback_result(
            "Slack OAuth not configured",
            "HedgeSpark's Slack OAuth isn't configured on this server. Ask support to set SLACK_CLIENT_ID/SECRET.",
            ok=False,
        )

    # Exchange code for token + webhook URL.
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": _slack_redirect_uri(),
                },
            )
        data = resp.json()
    except Exception as exc:
        log.warning("merchant_slack: oauth exchange failed for %s: %s", shop, exc)
        return _render_callback_result(
            "Couldn't reach Slack",
            "The Slack token exchange failed. Try again in a moment.",
            ok=False,
        )

    if not data.get("ok"):
        err = data.get("error", "unknown_error")
        log.warning("merchant_slack: oauth.v2.access error for %s: %s", shop, err)
        return _render_callback_result(
            "Slack rejected the connection",
            f"Slack returned an error: {err}. Nothing was saved.",
            ok=False,
        )

    webhook = (data.get("incoming_webhook") or {}).get("url", "").strip()
    channel = (data.get("incoming_webhook") or {}).get("channel", "").strip()
    if not webhook:
        return _render_callback_result(
            "No webhook returned",
            "Slack didn't return a webhook URL. Please try again.",
            ok=False,
        )

    ok, err = slack_dispatcher.save_webhook(db, shop, webhook)
    if not ok:
        log.warning("merchant_slack: save_webhook failed for %s: %s", shop, err)
        return _render_callback_result(
            "Webhook couldn't be saved",
            f"We couldn't save the connection: {err}",
            ok=False,
        )

    # Test-post into the channel as confirmation — same as /connect.
    channel_hint = f" in *#{channel}*" if channel else ""
    slack_dispatcher.post_message(
        db,
        shop,
        f":spark: *HedgeSpark connected{channel_hint}.*\nYour morning brief will arrive here daily around 08:00 Europe/Rome.",
    )

    return _render_callback_result(
        "Slack connected",
        f"Your morning brief will arrive here{channel_hint} daily around 08:00 Europe/Rome.",
        ok=True,
    )
