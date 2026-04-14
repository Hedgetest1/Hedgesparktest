"""signal_webhooks.py — GET/POST/DELETE /pro/signal-webhooks API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["signal_webhooks"])


class WebhookTestResult(BaseModel):
    status: str
    http_status: int | None = None
    attempts: int
    error: str | None = None


class WebhookTestResponse(BaseModel):
    webhook_id: str
    results: list[WebhookTestResult] = Field(default_factory=list)


class WebhookPayload(BaseModel):
    url: str = Field(..., max_length=2048, description="HTTPS webhook URL")
    events: list[str] = Field(..., min_length=1, max_length=50,
                               description="Signal event types to subscribe to")


class WebhookRow(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: str
    last_delivery_at: str | None = None
    last_delivery_status: str | None = None


class WebhookListResponse(BaseModel):
    shop_domain: str
    webhooks: list[WebhookRow] = Field(default_factory=list)
    available_events: list[str] = Field(default_factory=list)


class WebhookCreateResponse(BaseModel):
    webhook: WebhookRow
    signing_secret: str = Field(
        ..., description="HMAC-SHA256 secret for verifying incoming signatures"
    )
    signature_header: str = "X-HedgeSpark-Signature"


@router.get(
    "/pro/signal-webhooks",
    response_model=WebhookListResponse,
    response_model_exclude_none=False,
)
def list_webhooks_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """List all outbound signal webhooks registered by the shop."""
    from app.services.signal_webhooks import list_webhooks, SIGNAL_EVENTS
    webhooks = list_webhooks(shop)
    return WebhookListResponse(
        shop_domain=shop,
        webhooks=[WebhookRow(**w.to_dict()) for w in webhooks],
        available_events=sorted(SIGNAL_EVENTS),
    )


@router.post(
    "/pro/signal-webhooks",
    response_model=WebhookCreateResponse,
    response_model_exclude_none=False,
)
def create_webhook_endpoint(
    payload: WebhookPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Register a new outbound webhook. Returns the webhook row plus the
    shop's HMAC signing secret — store it server-side to verify incoming
    signatures on the `X-HedgeSpark-Signature` header.
    """
    from app.services.signal_webhooks import create_webhook, get_or_create_secret
    try:
        wh = create_webhook(shop, url=payload.url, events=payload.events)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if wh is None:
        raise HTTPException(status_code=503, detail="webhook storage unavailable")
    secret = get_or_create_secret(shop)
    return WebhookCreateResponse(
        webhook=WebhookRow(**wh.to_dict()),
        signing_secret=secret,
    )


@router.delete("/pro/signal-webhooks/{webhook_id}", response_model=OkResponse)
def delete_webhook_endpoint(
    webhook_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Delete a webhook by id."""
    from app.services.signal_webhooks import delete_webhook
    removed = delete_webhook(shop, webhook_id)
    if not removed:
        raise HTTPException(status_code=404, detail="webhook not found")
    return {"deleted": True, "id": webhook_id}


@router.post("/pro/signal-webhooks/{webhook_id}/test", response_model=WebhookTestResponse)
def test_webhook_endpoint(
    webhook_id: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Send a `test_ping` event to a specific webhook so the merchant can
    verify their integration before real signals arrive.
    """
    from app.services.signal_webhooks import (
        list_webhooks, emit_signal,
    )
    webhooks = list_webhooks(shop)
    target = next((w for w in webhooks if w.id == webhook_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="webhook not found")
    if "test_ping" not in target.events:
        # For test_ping we bypass the event filter — merchants want to test
        # without having to subscribe explicitly
        target.events = target.events + ["test_ping"]
    results = emit_signal(
        shop,
        event_type="test_ping",
        payload={"message": "HedgeSpark test ping — your webhook is wired up correctly."},
        source="manual_test",
    )
    return {
        "webhook_id": webhook_id,
        "results": [
            {
                "status": r.status,
                "http_status": r.http_status,
                "attempts": r.attempts,
                "error": r.error,
            }
            for r in results
        ],
    }
