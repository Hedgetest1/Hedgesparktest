"""
merchant_slack.py — Slack integration endpoints for the merchant.

Strada 3.5 (2026-04-20). Three endpoints:
  GET  /merchant/slack/status   — connected / error / not_connected
  POST /merchant/slack/connect  — save webhook URL + send test
  POST /merchant/slack/test     — re-send test (after a saved URL)
  DELETE /merchant/slack         — disconnect

Never returns the webhook URL itself in any response — only the
status. The URL is write-only from the dashboard's perspective.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
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
