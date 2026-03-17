from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class EventCreate(BaseModel):
    visitor_id: str
    session_id: Optional[str] = None

    event_type: str

    page_url: Optional[str] = None
    page_title: Optional[str] = None

    source_type: Optional[str] = None
    referrer: Optional[str] = None

    dwell_seconds: Optional[int] = None
    scroll_depth: Optional[int] = None

    event_data: Optional[Dict[str, Any]] = None

    occurred_at: Optional[datetime] = None
