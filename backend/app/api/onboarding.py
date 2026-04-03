"""
onboarding.py — Onboarding funnel event tracking endpoint.

POST /onboarding/event   — record a single onboarding event (merchant-facing)

Auth: merchant session cookie (require_merchant_session)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingEventRequest(BaseModel):
    event_type: str = Field(..., max_length=64)
    session_number: int | None = Field(None, ge=0, le=9999)
    context: dict | None = Field(None)


@router.post("/event")
def post_onboarding_event(
    body: OnboardingEventRequest,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Record a single onboarding event for the authenticated merchant.

    Milestone events are idempotent (recorded once per shop).
    Interaction events allow duplicates.
    """
    from app.services.onboarding_funnel import record_event, ALL_EVENT_TYPES

    if body.event_type not in ALL_EVENT_TYPES:
        return {"status": "ignored", "reason": f"unknown event_type: {body.event_type}"}

    event = record_event(
        db,
        shop_domain=shop,
        event_type=body.event_type,
        session_number=body.session_number,
        context=body.context,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # Unique constraint violation from partial index = race-condition dedup
        exc_str = str(exc).lower()
        if "uq_onboarding_milestone_per_shop" in exc_str or "unique" in exc_str:
            log.info("onboarding event dedup (constraint) for shop=%s type=%s", shop, body.event_type)
            return {"status": "duplicate", "event_id": None}
        log.exception("onboarding event commit failed for shop=%s type=%s", shop, body.event_type)
        return {"status": "error"}

    return {
        "status": "ok" if event else "duplicate",
        "event_id": event.id if event else None,
    }
