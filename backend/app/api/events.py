from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.event import Event
from app.schemas.event import EventCreate
from app.services.intent_engine import update_visitor_product_state

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
        session_id=payload.session_id,
        event_type=payload.event_type,
        page_url=payload.page_url,
        page_title=payload.page_title,
        source_type=payload.source_type,
        referrer=payload.referrer,
        dwell_seconds=payload.dwell_seconds,
        scroll_depth=payload.scroll_depth,
        event_data=payload.event_data,
        occurred_at=payload.occurred_at
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    update_visitor_product_state(db, event)

    return {
        "message": "event tracked",
        "event_id": event.id
    }
