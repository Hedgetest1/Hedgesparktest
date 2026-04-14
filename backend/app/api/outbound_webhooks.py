"""
outbound_webhooks.py — Phase Ω merchant-facing webhook CRUD API.

  POST   /pro/webhooks/subscriptions       — create
  GET    /pro/webhooks/subscriptions       — list
  PATCH  /pro/webhooks/subscriptions/{id}  — pause / resume / change events
  DELETE /pro/webhooks/subscriptions/{id}  — disable
  GET    /pro/webhooks/deliveries          — recent delivery log
  POST   /pro/webhooks/deliveries/{id}/replay — manual retry
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session
from app.models.outbound_webhook import (
    OutboundWebhookDelivery,
    OutboundWebhookSubscription,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["outbound_webhooks"])


SUPPORTED_EVENTS = (
    "nudge.fired",
    "nudge.dismissed",
    "rars.spike",
    "goal.at_risk",
    "anomaly.detected",
    "refund.processed",
    "trust_contract.executed",
    "compliance.alert",
    "*",
)


class SubscriptionIn(BaseModel):
    target_url: HttpUrl
    event_types: list[str] = Field(..., min_length=1, max_length=20)
    description: str | None = Field(default=None, max_length=200)


class SubscriptionPatch(BaseModel):
    event_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    status: Literal["active", "paused"] | None = None
    description: str | None = Field(default=None, max_length=200)


class SubscriptionOut(BaseModel):
    id: int
    target_url: str
    event_types: list[str]
    status: str
    description: str | None = None
    secret_preview: str  # last 4 chars only
    consecutive_failures: int
    auto_disabled: bool
    last_success_at: str | None = None
    last_failure_at: str | None = None
    created_at: str | None = None
    secret_revealed_once: str | None = None  # only on creation


class SubscriptionListResponse(BaseModel):
    subscriptions: list[SubscriptionOut] = Field(default_factory=list)


class DeliveryRow(BaseModel):
    id: int
    subscription_id: int
    event_type: str
    event_id: str | None = None
    status: str
    attempts: int
    response_status: int | None = None
    last_attempted_at: str | None = None
    delivered_at: str | None = None
    created_at: str | None = None


class DeliveriesListResponse(BaseModel):
    deliveries: list[DeliveryRow] = Field(default_factory=list)


class ReplayResponse(BaseModel):
    id: int
    result: dict | None = None


def _to_out(s: OutboundWebhookSubscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=s.id,
        target_url=s.target_url,
        event_types=list(s.event_types or []),
        status=s.status,
        description=s.description,
        secret_preview="…" + (s.secret or "")[-4:],
        consecutive_failures=s.consecutive_failures or 0,
        auto_disabled=bool(s.auto_disabled),
        last_success_at=s.last_success_at.isoformat() if s.last_success_at else None,
        last_failure_at=s.last_failure_at.isoformat() if s.last_failure_at else None,
        created_at=s.created_at.isoformat() if s.created_at else None,
    )


@router.post("/pro/webhooks/subscriptions", response_model=SubscriptionOut)
def create_subscription(
    payload: SubscriptionIn,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    bad = [e for e in payload.event_types if e not in SUPPORTED_EVENTS]
    if bad:
        raise HTTPException(status_code=400, detail=f"unsupported_event_types: {bad}")
    from app.services.outbound_webhooks import create_subscription as svc_create
    sub = svc_create(
        db,
        shop_domain=shop,
        target_url=str(payload.target_url),
        event_types=payload.event_types,
        description=payload.description,
    )
    out = _to_out(sub).model_dump()
    out["secret_revealed_once"] = sub.secret  # only on creation
    return out


@router.get("/pro/webhooks/subscriptions", response_model=SubscriptionListResponse)
def list_subscriptions(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(OutboundWebhookSubscription)
        .filter(OutboundWebhookSubscription.shop_domain == shop)
        .order_by(OutboundWebhookSubscription.id.desc())
        .all()
    )
    return {"subscriptions": [_to_out(r).model_dump() for r in rows]}


@router.patch("/pro/webhooks/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(
    sub_id: int,
    payload: SubscriptionPatch,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    sub = (
        db.query(OutboundWebhookSubscription)
        .filter(
            OutboundWebhookSubscription.id == sub_id,
            OutboundWebhookSubscription.shop_domain == shop,
        )
        .one_or_none()
    )
    if not sub:
        raise HTTPException(status_code=404, detail="not_found")
    if payload.event_types is not None:
        bad = [e for e in payload.event_types if e not in SUPPORTED_EVENTS]
        if bad:
            raise HTTPException(status_code=400, detail=f"unsupported_event_types: {bad}")
        sub.event_types = payload.event_types
    if payload.status is not None:
        sub.status = payload.status
    if payload.description is not None:
        sub.description = payload.description
    db.flush()
    return _to_out(sub).model_dump()


@router.delete("/pro/webhooks/subscriptions/{sub_id}", response_model=OkResponse)
def delete_subscription(
    sub_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.outbound_webhooks import revoke_subscription
    ok = revoke_subscription(db, shop, sub_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True}


@router.get("/pro/webhooks/deliveries", response_model=DeliveriesListResponse)
def list_deliveries(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = None,
):
    q = (
        db.query(OutboundWebhookDelivery)
        .filter(OutboundWebhookDelivery.shop_domain == shop)
    )
    if status:
        q = q.filter(OutboundWebhookDelivery.status == status)
    rows = q.order_by(OutboundWebhookDelivery.id.desc()).limit(limit).all()
    return {
        "deliveries": [
            {
                "id": d.id,
                "subscription_id": d.subscription_id,
                "event_type": d.event_type,
                "event_id": d.event_id,
                "status": d.status,
                "attempts": d.attempts,
                "response_status": d.response_status,
                "last_attempted_at": d.last_attempted_at.isoformat() if d.last_attempted_at else None,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ]
    }


@router.post("/pro/webhooks/deliveries/{delivery_id}/replay", response_model=ReplayResponse)
def replay_delivery(
    delivery_id: int,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    d = (
        db.query(OutboundWebhookDelivery)
        .filter(
            OutboundWebhookDelivery.id == delivery_id,
            OutboundWebhookDelivery.shop_domain == shop,
        )
        .one_or_none()
    )
    if not d:
        raise HTTPException(status_code=404, detail="not_found")
    d.status = "pending"
    db.flush()
    from app.services.outbound_webhooks import attempt_delivery
    result = attempt_delivery(db, d.id)
    return {"id": d.id, "result": result}
