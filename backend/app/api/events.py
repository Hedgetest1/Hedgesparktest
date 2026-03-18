"""
POST /track-event — legacy event ingestion endpoint.

Accepts the EventCreate schema and writes to the events table using
the real column names. update_visitor_product_state has been removed
from this endpoint because the intent engine accesses fields that no
longer exist on the Event model; that integration is a separate task.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.event import Event
from app.schemas.event import EventCreate

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/track-event")
def track_event(payload: EventCreate, db: Session = Depends(get_db)):
    event = Event(
        visitor_id=payload.visitor_id,
        event_type=payload.event_type,
        url=payload.page_url,
        dwell_seconds=payload.dwell_seconds,
        max_scroll_depth=payload.scroll_depth,
        shop_domain=payload.shop_domain if hasattr(payload, "shop_domain") else "legacy.myshopify.com",
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return {"message": "event tracked", "event_id": event.id}
