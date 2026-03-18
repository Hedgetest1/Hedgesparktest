"""
POST /track — storefront event ingestion endpoint for Hedge Spark.

Receives events from tracker.js, upserts a Visitor row, then persists an Event row.

Column mapping (real events table schema):
  payload.page_url or payload.product_url → Event.url
  payload.timestamp                        → Event.timestamp  (epoch ms, bigint)
  payload.dwell_seconds                    → Event.dwell_seconds
  payload.scroll_depth                     → Event.max_scroll_depth
  payload.shop_domain                      → Event.shop_domain
  payload.visitor_id                       → Event.visitor_id
  payload.event_type                       → Event.event_type

product_url: on Shopify product pages the page URL is the product URL.
The tracker sends product_url only on product_view events; we store it
in the url column (same field) so no extra column is required.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.event import Event
from app.models.visitor import Visitor
from app.services.shopify_auth import is_valid_shop_domain

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TrackPayload(BaseModel):
    shop_domain: str
    visitor_id: str
    event_type: str
    page_url: Optional[str] = None
    product_url: Optional[str] = None   # present on product_view events
    timestamp: Optional[int] = None     # epoch milliseconds
    dwell_seconds: Optional[int] = None
    scroll_depth: Optional[int] = None  # mapped to max_scroll_depth column


def _upsert_visitor(db: Session, visitor_id: str, shop_domain: str) -> None:
    """Create a Visitor row if new; otherwise bump last_seen."""
    visitor = (
        db.query(Visitor)
        .filter(Visitor.visitor_id == visitor_id, Visitor.shop_domain == shop_domain)
        .first()
    )
    now = datetime.utcnow()
    if visitor is None:
        db.add(Visitor(visitor_id=visitor_id, shop_domain=shop_domain, first_seen=now, last_seen=now))
    else:
        visitor.last_seen = now


@router.post("/track")
def track_event(payload: TrackPayload, db: Session = Depends(get_db)):
    """
    Ingest a single storefront event from tracker.js.

    shop_domain must be a valid *.myshopify.com domain.
    """
    if not is_valid_shop_domain(payload.shop_domain):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com domain.",
        )

    _upsert_visitor(db, payload.visitor_id, payload.shop_domain)

    # product_url takes priority on product_view events; otherwise use page_url
    url = payload.product_url or payload.page_url

    event = Event(
        shop_domain=payload.shop_domain,
        visitor_id=payload.visitor_id,
        event_type=payload.event_type,
        url=url,
        timestamp=payload.timestamp,
        dwell_seconds=payload.dwell_seconds,
        max_scroll_depth=payload.scroll_depth,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return {"status": "ok", "event_id": event.id}
