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

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.url_utils import normalize_product_url
from app.models.event import Event
from app.models.shop_order import ShopOrder
from app.models.visitor import Visitor
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.shopify_auth import is_valid_shop_domain

import logging

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# CORS for storefront & pixel requests
#
# /track and /track/batch are called cross-origin from:
#   - spark-tracker.js on *.myshopify.com storefronts
#   - spark-pixel.js in Shopify's Custom Pixel sandbox (unpredictable origin)
#
# The main CORSMiddleware only allows app.hedgesparkhq.com (dashboard).
# These routes need Access-Control-Allow-Origin: * so cross-origin fetch
# with *: application/json passes the browser preflight check.
#
# Safe because: no cookies/credentials are used (tracker sends credentials: "omit"),
# the payload is validated (known shop, rate-limited, schema-checked), and
# the response contains no sensitive data.
# ---------------------------------------------------------------------------
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


@router.options("/track")
@router.options("/track/batch")
async def track_cors_preflight():
    """Handle CORS preflight for storefront and pixel callers."""
    return Response(status_code=204, headers=_CORS_HEADERS)


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
    "begin_checkout",
    "view_cart",
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
    utm_medium: Optional[str] = None     # raw utm_medium for paid/organic classification

    # Full UTM parameters — captured from URL query string by tracker.
    utm_source: Optional[str] = None     # e.g., google, facebook, newsletter
    utm_campaign: Optional[str] = None   # campaign name
    utm_content: Optional[str] = None    # ad variant / creative
    utm_term: Optional[str] = None       # search keyword

    # Click ID — ad platform identifiers. Stored as "type:value".
    # Tracker sends whichever is present: gclid, fbclid, ttclid, msclkid.
    click_id: Optional[str] = None

    # Landing page — first page URL of the visit (set by tracker on first page_view).
    landing_page: Optional[str] = None

    # Device type — "mobile" or "desktop", sent by tracker since v3.
    device_type: Optional[str] = None

    # Shopify numeric product ID — sent on product pages since migration o1a2b3c4d5e6.
    # Sourced from window.ShopifyAnalytics.meta.product.id by spark-tracker.js.
    # Used to resolve product_url at order ingestion time for real conversion metrics.
    product_id: Optional[str] = None    # Shopify integer product ID, stored as string

    # Purchase fields — sent by spark-tracker.js on the Shopify thank-you page.
    # Replaces Shopify webhooks (orders/*) which require Protected Customer Data approval.
    order_id: Optional[str] = None       # Shopify order ID (from Shopify.checkout.order_id)
    order_total: Optional[float] = None  # total_price as float
    currency: Optional[str] = None       # ISO 4217 currency code (e.g. "EUR", "USD")

    # Identity bridge — sent by the pixel when it reads the _hs_vid cookie.
    # This is the storefront tracker's visitor_id, bridging browsing → purchase.
    tracker_visitor_id: Optional[str] = None

    # Per-merchant pixel secret — validated on purchase events to prevent spoofing.
    pixel_secret: Optional[str] = None


def _check_per_shop_rate(request, shop_domain: str) -> bool:
    """
    Per-IP + per-shop rate limit: max 60 events per 60 seconds per (IP, shop).

    This catches the scenario where a single IP floods events for one shop
    while staying under the global /track rate limit (which is per-IP only).

    Uses Redis when available; silently allows when Redis is down.
    """
    try:
        from app.core.redis_client import _client
        client = _client()
        if client is None:
            return True
        ip = request.client.host if request.client else "unknown"
        key = f"hs:rl:track:{ip}:{shop_domain}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        return count <= 60
    except Exception:
        return True  # fail open


def _is_known_shop(db: Session, shop_domain: str) -> bool:
    """
    Check if shop_domain belongs to a known installed merchant.

    Uses Redis cache (5-min TTL) to avoid DB hit per event.
    This is the primary tracker abuse protection — prevents forged
    events for shops that never installed Hedge Spark.
    """
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:known_shop:{shop_domain}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from app.models.merchant import Merchant
    exists = (
        db.query(Merchant)
        .filter(Merchant.shop_domain == shop_domain, Merchant.install_status == "active")
        .first()
    ) is not None
    cache_set(cache_key, exists, 300)  # 5 min TTL
    return exists


def _upsert_visitor(db: Session, visitor_id: str, shop_domain: str) -> None:
    """Create a Visitor row if new; otherwise bump last_seen.

    Race-safe: concurrent INSERTs for the same (visitor_id, shop_domain)
    are caught via SAVEPOINT + IntegrityError recovery.  The losing request
    falls through to an UPDATE on the existing row.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    visitor = (
        db.query(Visitor)
        .filter(Visitor.visitor_id == visitor_id, Visitor.shop_domain == shop_domain)
        .first()
    )
    if visitor is not None:
        visitor.last_seen = now
        return

    try:
        nested = db.begin_nested()  # SAVEPOINT
        db.add(Visitor(visitor_id=visitor_id, shop_domain=shop_domain, first_seen=now, last_seen=now))
        db.flush()
    except IntegrityError:
        nested.rollback()
        # Another request won the INSERT race — update the existing row
        visitor = (
            db.query(Visitor)
            .filter(Visitor.visitor_id == visitor_id, Visitor.shop_domain == shop_domain)
            .first()
        )
        if visitor is not None:
            visitor.last_seen = now


def _persist_purchase(db: Session, payload: TrackPayload) -> None:
    """
    Persist a client-side purchase event into shop_orders.

    This replaces Shopify webhooks (orders/*) for MVP — all order topics
    require Protected Customer Data approval which blocks MVP validation.

    Idempotent: duplicate order_id is silently skipped via the existing
    UNIQUE constraint on shopify_order_id.
    """
    if payload.event_type != "purchase":
        return
    if not payload.order_id or not payload.order_total or payload.order_total <= 0:
        return

    # Validate pixel_secret against the merchant's stored secret.
    # If the merchant has a pixel_secret set, the pixel MUST send the matching value.
    # This prevents spoofed purchase events from poisoning revenue data.
    # Merchants without pixel_secret (installed before this field) are allowed through.
    from app.models.merchant import Merchant
    merchant = db.query(Merchant).filter(
        Merchant.shop_domain == payload.shop_domain
    ).first()
    if merchant and merchant.pixel_secret:
        if not payload.pixel_secret or payload.pixel_secret != merchant.pixel_secret:
            log.warning(
                "track/purchase: pixel_secret mismatch shop=%s order_id=%s — rejected",
                payload.shop_domain, payload.order_id,
            )
            return

    existing = (
        db.query(ShopOrder)
        .filter(ShopOrder.shopify_order_id == str(payload.order_id))
        .first()
    )
    if existing:
        log.info(
            "track/purchase: duplicate skipped order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    order = ShopOrder(
        shop_domain=payload.shop_domain,
        shopify_order_id=str(payload.order_id),
        total_price=payload.order_total,
        currency=(payload.currency or "EUR").upper(),
        customer_id=None,       # not available client-side
        customer_email=None,    # not available client-side
        line_items=[],          # not available client-side
        created_at=now,
        ingested_at=now,
        source="pixel",
    )
    try:
        nested = db.begin_nested()  # SAVEPOINT — won't kill the outer transaction
        db.add(order)
        db.flush()
        log.info(
            "track/purchase: stored order_id=%s shop=%s total=%.2f %s",
            payload.order_id, payload.shop_domain,
            payload.order_total, order.currency,
        )
    except IntegrityError:
        nested.rollback()
        log.info(
            "track/purchase: duplicate skipped (race) order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
    except Exception as exc:
        nested.rollback()
        log.error(
            "track/purchase: unexpected error order_id=%s shop=%s: %s",
            payload.order_id, payload.shop_domain, exc,
        )

    # --- Identity bridge: link tracker visitor_id → order ---
    # When the pixel reads the _hs_vid cookie (set by spark-tracker.js),
    # it sends tracker_visitor_id.  This is the storefront browsing identity.
    # Writing a VisitorPurchaseSession row creates the join path:
    #   events (tracker visitor_id) → visitor_purchase_sessions → shop_orders
    _persist_visitor_bridge(db, payload)


def _persist_visitor_bridge(db: Session, payload: TrackPayload) -> None:
    """
    Create a visitor_purchase_sessions row linking the storefront tracker
    identity to the purchase order.  This is the identity bridge.

    Only fires when tracker_visitor_id is present (pixel read the _hs_vid cookie).
    Idempotent: UNIQUE constraint on shopify_order_id prevents duplicates.
    """
    if not payload.tracker_visitor_id or not payload.order_id:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        nested = db.begin_nested()
        db.add(VisitorPurchaseSession(
            shop_domain=payload.shop_domain,
            visitor_id=payload.tracker_visitor_id,
            shopify_order_id=str(payload.order_id),
            confirmed_at=now,
            ingested_at=now,
        ))
        db.flush()
        log.info(
            "track/bridge: linked tracker_vid=%s → order_id=%s shop=%s",
            payload.tracker_visitor_id, payload.order_id, payload.shop_domain,
        )
    except IntegrityError:
        nested.rollback()
        log.info(
            "track/bridge: duplicate skipped order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
    except Exception as exc:
        nested.rollback()
        log.error(
            "track/bridge: unexpected error order_id=%s shop=%s: %s",
            payload.order_id, payload.shop_domain, exc,
        )


@router.post("/track")
def track_event(request: Request, payload: TrackPayload, db: Session = Depends(get_db)):
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

    # Anti-abuse: verify the shop is a known installed merchant.
    # This prevents attackers from fabricating events for arbitrary domains.
    # Cached in Redis for 5 minutes to avoid DB lookup per event.
    if not _is_known_shop(db, payload.shop_domain):
        raise HTTPException(
            status_code=400,
            detail="Unknown shop.",
        )

    # Per-IP + per-shop rate limit: 60 events/min per (IP, shop) combination.
    if not _check_per_shop_rate(request, payload.shop_domain):
        raise HTTPException(
            status_code=429,
            detail="Too many events for this shop.",
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
        utm_medium=payload.utm_medium or None,
        # Full UTM parameters — None when not provided by tracker.
        utm_source=payload.utm_source[:128] if payload.utm_source else None,
        utm_campaign=payload.utm_campaign[:256] if payload.utm_campaign else None,
        utm_content=payload.utm_content[:256] if payload.utm_content else None,
        utm_term=payload.utm_term[:256] if payload.utm_term else None,
        # Click ID — ad platform identifier (gclid:xxx, fbclid:yyy)
        click_id=payload.click_id[:256] if payload.click_id else None,
        # Landing page — first page URL of the visit
        landing_page=payload.landing_page[:512] if payload.landing_page else None,
        # product_id: None on non-product pages; Shopify integer ID (as string) on product pages.
        product_id=payload.product_id or None,
        # device_type: "mobile" or "desktop", nullable for older events
        device_type=payload.device_type if payload.device_type in ("mobile", "desktop") else None,
    )

    db.add(event)

    # Purchase events also persist to shop_orders for revenue analytics
    _persist_purchase(db, payload)

    db.commit()

    return JSONResponse(
        content={"status": "ok", "event_id": event.id},
        headers=_CORS_HEADERS,
    )


# ---------------------------------------------------------------------------
# Batch ingestion — POST /track/batch
#
# Accepts { events: [...] } with up to 50 events per request.
# Single transaction, single commit — 10-50x fewer DB round-trips
# compared to individual /track calls.
#
# Each event in the array uses the same TrackPayload schema.
# Invalid events are skipped (logged) without aborting the batch.
# The response reports accepted count and any rejections.
# ---------------------------------------------------------------------------

class BatchTrackPayload(BaseModel):
    events: list[TrackPayload]


@router.post("/track/batch")
def track_event_batch(payload: BatchTrackPayload, db: Session = Depends(get_db)):
    """
    Ingest a batch of storefront events in a single transaction.

    Accepts up to 50 events.  Invalid events are skipped, not rejected.
    Returns count of accepted vs rejected events.
    """
    MAX_BATCH = 50
    events_list = payload.events[:MAX_BATCH]
    accepted = 0
    rejected = 0

    # Deduplicate visitor upserts within the batch
    seen_visitors: set[tuple[str, str]] = set()

    for item in events_list:
        if not is_valid_shop_domain(item.shop_domain):
            rejected += 1
            continue
        if item.event_type not in _ALLOWED_EVENT_TYPES:
            rejected += 1
            continue

        vkey = (item.visitor_id, item.shop_domain)
        if vkey not in seen_visitors:
            _upsert_visitor(db, item.visitor_id, item.shop_domain)
            seen_visitors.add(vkey)

        canonical_product_url = normalize_product_url(item.product_url)
        db.add(Event(
            shop_domain=item.shop_domain,
            visitor_id=item.visitor_id,
            event_type=item.event_type,
            url=item.page_url,
            product_url=canonical_product_url,
            timestamp=item.timestamp,
            dwell_seconds=item.dwell_seconds,
            max_scroll_depth=item.scroll_depth,
            source_type=item.source_type or None,
            referrer=item.referrer or None,
            product_id=item.product_id or None,
            device_type=item.device_type if item.device_type in ("mobile", "desktop") else None,
        ))

        # Purchase events also persist to shop_orders
        _persist_purchase(db, item)

        accepted += 1

    if accepted > 0:
        db.commit()

    return JSONResponse(
        content={"status": "ok", "accepted": accepted, "rejected": rejected},
        headers=_CORS_HEADERS,
    )

from fastapi import Response

@router.options("/track")
async def options_track():
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

@router.options("/track/batch")
async def options_track_batch():
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

