"""
POST /track-event — legacy event ingestion endpoint used by wishspark.js widget.

Accepts the EventCreate schema and writes to the events table.
source_type and referrer are now persisted (previously they were silently
discarded because the Event model lacked those columns).

shop_domain is not sent by the current widget build, so we fall back to
"legacy.myshopify.com".  When the widget is updated to send shop_domain,
the schema field will carry it through automatically.
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
    # shop_domain: use whatever the widget sent; fall back for legacy builds.
    shop_domain = payload.shop_domain or "legacy.myshopify.com"

    event = Event(
        visitor_id=payload.visitor_id,
        event_type=payload.event_type,
        url=payload.page_url,
        dwell_seconds=payload.dwell_seconds,
        max_scroll_depth=payload.scroll_depth,
        shop_domain=shop_domain,
        # Source attribution — now persisted instead of silently discarded.
        source_type=payload.source_type or None,
        referrer=payload.referrer or None,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return {"message": "event tracked", "event_id": event.id}
