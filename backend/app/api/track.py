"""
POST /track — storefront event ingestion endpoint for Hedge Spark.

Receives events from spark-tracker.js, upserts a Visitor row, then persists
an Event row with all fields stored in their dedicated columns.

Column mapping (events table schema):
  payload.page_url    → Event.url          (raw page URL, always present)
  payload.product_url → Event.product_url  (canonical product path, NULL on non-product pages)
  payload.timestamp   → Event.timestamp    (epoch ms, bigint)
  payload.dwell_seconds   → Event.dwell_seconds
  payload.scroll_depth    → Event.max_scroll_depth
  payload.shop_domain     → Event.shop_domain
  payload.visitor_id      → Event.visitor_id
  payload.event_type      → Event.event_type
  payload.source_type     → Event.source_type  (direct | google | facebook | …)
  payload.referrer        → Event.referrer     (raw document.referrer)

Design note — url vs product_url
---------------------------------
url       = raw page URL for every event (what page the visitor was on).
product_url = the canonical product path when the event fired on a product page;
              NULL for non-product pages (home, collection, checkout, etc.).
              Canonical format: /products/{handle}

Server-side normalization (defensive layer)
-------------------------------------------
Even though spark-tracker.js now sends path-only product_url values, we
normalize server-side as a safety net for:
  - old tracker versions still in browser caches
  - third-party integrations that send full URLs
  - manual API calls during development

normalize_product_url() extracts /products/{handle} from any input and
returns None for non-product values, so garbage never reaches the DB.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.url_utils import normalize_product_url
from app.models.event import Event
from app.models.visitor import Visitor
from app.services.shopify_auth import is_valid_shop_domain

router = APIRouter()

# Strict allowlist of event types accepted from the storefront tracker.
# Any value not in this set is rejected with HTTP 400.
# To add a new event type: update this set AND the corresponding tracker script.
_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "page_view",
    "product_view",
    "dwell_time",
    "scroll",
    "add_to_cart",
    "click",
    "page_leave",
    "wishlist_add",
    "purchase",
})


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
    page_url: Optional[str] = None       # raw page URL (always sent by tracker)
    product_url: Optional[str] = None    # product path; NULL on non-product pages
    timestamp: Optional[int] = None      # epoch milliseconds
    dwell_seconds: Optional[int] = None
    scroll_depth: Optional[int] = None   # mapped to max_scroll_depth column

    # Source attribution — sent by spark-tracker.js since migration j7e0a4b8c3d6.
    source_type: Optional[str] = None    # direct | google | facebook | …
    referrer: Optional[str] = None       # raw document.referrer (may be empty str)

    # Shopify numeric product ID — sent on product pages since migration o1a2b3c4d5e6.
    # Sourced from window.ShopifyAnalytics.meta.product.id by spark-tracker.js.
    # Used to resolve product_url at order ingestion time for real conversion metrics.
    product_id: Optional[str] = None    # Shopify integer product ID, stored as string


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
    Ingest a single storefront event from spark-tracker.js.

    shop_domain must be a valid *.myshopify.com domain.
    url and product_url are stored as separate columns.
    product_url is normalized to /products/{handle} before persistence.
    source_type and referrer are persisted when present.
    """
    if not is_valid_shop_domain(payload.shop_domain):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com domain.",
        )

    if payload.event_type not in _ALLOWED_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid event_type.",
        )

    _upsert_visitor(db, payload.visitor_id, payload.shop_domain)

    # Normalize defensively — handles old tracker versions, third-party senders,
    # and any full URL that slipped through. Returns None for non-product input.
    canonical_product_url = normalize_product_url(payload.product_url)

    event = Event(
        shop_domain=payload.shop_domain,
        visitor_id=payload.visitor_id,
        event_type=payload.event_type,
        url=payload.page_url,                    # raw page URL, always stored as-is
        product_url=canonical_product_url,       # None for non-product pages
        timestamp=payload.timestamp,
        dwell_seconds=payload.dwell_seconds,
        max_scroll_depth=payload.scroll_depth,
        # Store None rather than empty string for clean NULL checks in queries.
        source_type=payload.source_type or None,
        referrer=payload.referrer or None,
        # product_id: None on non-product pages; Shopify integer ID (as string) on product pages.
        product_id=payload.product_id or None,
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    return {"status": "ok", "event_id": event.id}
