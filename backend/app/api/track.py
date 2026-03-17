from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import engine

router = APIRouter()

class TrackEvent(BaseModel):
    visitor_id: str
    event: str
    url: str
    timestamp: int
    dwell_seconds: int | None = None
    max_scroll_depth: int | None = None

@router.post("/track")
def track_event(payload: TrackEvent):

    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO events
            (visitor_id, event_type, url, timestamp, dwell_seconds, max_scroll_depth)
            VALUES
            (:visitor_id, :event, :url, :timestamp, :dwell_seconds, :max_scroll_depth)
            """),
            {
                "visitor_id": payload.visitor_id,
                "event": payload.event,
                "url": payload.url,
                "timestamp": payload.timestamp,
                "dwell_seconds": payload.dwell_seconds,
                "max_scroll_depth": payload.max_scroll_depth
            }
        )

    return {"status":"ok"}
