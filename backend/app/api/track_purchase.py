"""
track_purchase.py — POST /track/purchase-confirmed

Receives visitor-to-order attribution events from spark-attribution.js running
on the Shopify Order Status (thank-you) page.

Each event carries:
    shop_domain       — merchant's myshopify.com domain
    visitor_id        — persistent UUID from localStorage (hedgespark_visitor_id)
    shopify_order_id  — Shopify's order ID from window.Shopify.checkout.order_id
    timestamp         — browser epoch milliseconds

This is the attribution bridge: it joins the persistent visitor behavioral
identity (established by spark-tracker.js on product pages) to a real Shopify
order (already stored in shop_orders via the orders/paid webhook).

Design decisions
----------------
Idempotency
    One attribution row per shopify_order_id is enforced via a UNIQUE constraint
    on visitor_purchase_sessions.shopify_order_id.  The endpoint returns
    {"status": "duplicate"} on a second delivery — never an error — because
    Shopify may retry webhook delivery and the merchant's thank-you page may be
    refreshed.

No product_url at attribution time
    The order's product_url(s) live in shop_orders.line_items (enriched by
    order_ingestion._enrich_line_items_with_product_url).  Duplicating them here
    adds no value and creates a second source of truth.  Callers that need
    product-level attribution join visitor_purchase_sessions → shop_orders.

No shop auth required
    This endpoint is called from the browser (spark-attribution.js), not from
    the Hedge Spark dashboard.  It does not carry a dashboard API key.
    shop_domain validation uses the existing is_valid_shop_domain() check —
    the same check applied in POST /track.

Logging
-------
    INFO  — attribution received (every call)
    INFO  — attribution stored (new row)
    INFO  — duplicate skipped (shopify_order_id already in table)
    WARNING — rejected payload (missing or invalid required fields)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(tags=["attribution"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PurchaseAttributionPayload(BaseModel):
    """
    Payload from spark-attribution.js on the Shopify thank-you page.

    All fields are required — the script only fires when all three identity
    anchors (shop_domain, visitor_id, shopify_order_id) are resolvable.
    """
    shop_domain:       str
    visitor_id:        str
    shopify_order_id:  str
    timestamp:         int   # epoch milliseconds from Date.now()


@router.post("/track/purchase-confirmed")
def track_purchase_confirmed(
    payload: PurchaseAttributionPayload,
    db: Session = Depends(get_db),
):
    """
    Receive and persist a visitor-to-order attribution event.

    Called by spark-attribution.js from the Shopify Order Status page.
    Stores one row in visitor_purchase_sessions per unique shopify_order_id.

    Returns
    -------
    {"status": "ok"}        — new attribution stored
    {"status": "duplicate"} — shopify_order_id already attributed; row unchanged

    HTTP 400 on:
    - Invalid shop_domain format (must be *.myshopify.com)
    - Empty visitor_id or shopify_order_id
    """
    # Validate shop domain format — same rule as POST /track
    if not is_valid_shop_domain(payload.shop_domain):
        log.warning(
            "track/purchase-confirmed: invalid shop_domain=%r — rejected",
            payload.shop_domain,
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com domain.",
        )

    # Validate required string fields — must be non-empty after strip
    visitor_id       = payload.visitor_id.strip()
    shopify_order_id = payload.shopify_order_id.strip()

    if not visitor_id:
        log.warning(
            "track/purchase-confirmed: empty visitor_id for shop=%s — rejected",
            payload.shop_domain,
        )
        raise HTTPException(status_code=400, detail="visitor_id must not be empty.")

    if not shopify_order_id:
        log.warning(
            "track/purchase-confirmed: empty shopify_order_id for shop=%s — rejected",
            payload.shop_domain,
        )
        raise HTTPException(status_code=400, detail="shopify_order_id must not be empty.")

    log.info(
        "track/purchase-confirmed: received visitor_id=%s order_id=%s shop=%s",
        visitor_id, shopify_order_id, payload.shop_domain,
    )

    # Convert browser epoch ms to UTC datetime for storage
    try:
        confirmed_at = datetime.fromtimestamp(payload.timestamp / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):
        # Pathological timestamp — use server now as a safe fallback
        confirmed_at = datetime.utcnow()

    row = VisitorPurchaseSession(
        shop_domain      = payload.shop_domain,
        visitor_id       = visitor_id,
        shopify_order_id = shopify_order_id,
        product_url      = None,   # populated in future by enrichment query
        confirmed_at     = confirmed_at,
        ingested_at      = datetime.utcnow(),
    )

    try:
        db.add(row)
        db.commit()
        log.info(
            "track/purchase-confirmed: stored — visitor_id=%s order_id=%s shop=%s",
            visitor_id, shopify_order_id, payload.shop_domain,
        )
        return {"status": "ok"}

    except IntegrityError:
        db.rollback()
        log.info(
            "track/purchase-confirmed: duplicate skipped — order_id=%s shop=%s",
            shopify_order_id, payload.shop_domain,
        )
        return {"status": "duplicate"}
