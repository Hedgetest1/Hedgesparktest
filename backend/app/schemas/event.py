from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class EventCreate(BaseModel):
    visitor_id: str
    session_id: Optional[str] = None

    event_type: str

    page_url: Optional[str] = None
    page_title: Optional[str] = None

    # shop_domain is optional here for backward compatibility with the legacy
    # widget (wishspark.js) which does not send it.  The /track-event endpoint
    # falls back to "legacy.myshopify.com" when this is absent.
    shop_domain: Optional[str] = None

    # Source attribution — populated by both trackers.
    source_type: Optional[str] = None   # direct | search | social | referral
    referrer: Optional[str] = None      # raw document.referrer

    dwell_seconds: Optional[int] = None
    scroll_depth: Optional[int] = None

    event_data: Optional[Dict[str, Any]] = None

    occurred_at: Optional[datetime] = None
