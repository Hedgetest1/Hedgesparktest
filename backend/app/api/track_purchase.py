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
order (already stored in shop_orders via the orders/updated webhook).

Attribution resolution:
    At conversion time, we query the visitor's event history to resolve:
    - first_source / first_campaign: from the visitor's earliest event
    - last_source / last_campaign: from the visitor's most recent event before purchase
    - attribution_evidence: JSON audit trail of the full chain

Design decisions
----------------
Idempotency
    One attribution row per shopify_order_id is enforced via a UNIQUE constraint
    on visitor_purchase_sessions.shopify_order_id.

No shop auth required
    Called from browser (spark-attribution.js), not from the dashboard.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
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


def _resolve_attribution(db: Session, shop_domain: str, visitor_id: str) -> dict:
    """
    Resolve first-touch and last-touch attribution for a visitor.
    Queries the visitor's event history and returns the attribution chain.

    Returns:
        {
            "first_source": str | None,
            "first_campaign": str | None,
            "last_source": str | None,
            "last_campaign": str | None,
            "evidence": {
                "first_event_ts": int | None,
                "first_referrer": str | None,
                "first_landing_page": str | None,
                "first_click_id": str | None,
                "last_event_ts": int | None,
                "last_referrer": str | None,
                "last_click_id": str | None,
                "total_events": int,
                "distinct_sources": list[str],
            }
        }
    """
    result = {
        "first_source": None,
        "first_campaign": None,
        "last_source": None,
        "last_campaign": None,
        "evidence": {},
    }

    try:
        # First-touch: earliest event for this visitor
        first = db.execute(text("""
            SELECT source_type, utm_campaign, utm_source, referrer, landing_page, click_id, timestamp
            FROM events
            WHERE shop_domain = :shop AND visitor_id = :vid AND source_type IS NOT NULL
            ORDER BY timestamp ASC
            LIMIT 1
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        # Last-touch: most recent event with source data
        last = db.execute(text("""
            SELECT source_type, utm_campaign, utm_source, referrer, click_id, timestamp
            FROM events
            WHERE shop_domain = :shop AND visitor_id = :vid AND source_type IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        # Distinct sources and total event count
        stats = db.execute(text("""
            SELECT COUNT(*) AS total,
                   ARRAY_AGG(DISTINCT source_type) FILTER (WHERE source_type IS NOT NULL) AS sources
            FROM events
            WHERE shop_domain = :shop AND visitor_id = :vid
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        if first:
            result["first_source"] = first[0]
            result["first_campaign"] = first[1] or first[2]  # utm_campaign or utm_source
            result["evidence"]["first_event_ts"] = first[6]
            result["evidence"]["first_referrer"] = first[3]
            result["evidence"]["first_landing_page"] = first[4]
            result["evidence"]["first_click_id"] = first[5]

        if last:
            result["last_source"] = last[0]
            result["last_campaign"] = last[1] or last[2]  # utm_campaign or utm_source
            result["evidence"]["last_event_ts"] = last[5]
            result["evidence"]["last_referrer"] = last[3]
            result["evidence"]["last_click_id"] = last[4]

        if stats:
            result["evidence"]["total_events"] = stats[0] or 0
            result["evidence"]["distinct_sources"] = list(stats[1] or [])

    except Exception as exc:
        log.warning("track/purchase: attribution resolution failed for %s:%s: %s",
                    shop_domain, visitor_id, exc)

    return result


@router.post("/track/purchase-confirmed")
def track_purchase_confirmed(
    payload: PurchaseAttributionPayload,
    db: Session = Depends(get_db),
):
    """
    Receive and persist a visitor-to-order attribution event.

    Called by spark-attribution.js from the Shopify Order Status page.
    Stores one row in visitor_purchase_sessions per unique shopify_order_id.
    Resolves first-touch and last-touch attribution at conversion time.

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
        confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Resolve attribution from visitor's event history
    attr = _resolve_attribution(db, payload.shop_domain, visitor_id)

    row = VisitorPurchaseSession(
        shop_domain      = payload.shop_domain,
        visitor_id       = visitor_id,
        shopify_order_id = shopify_order_id,
        product_url      = None,   # populated in future by enrichment query
        confirmed_at     = confirmed_at,
        ingested_at      = datetime.now(timezone.utc).replace(tzinfo=None),
        # Attribution snapshots
        first_source     = attr["first_source"],
        first_campaign   = attr["first_campaign"],
        last_source      = attr["last_source"],
        last_campaign    = attr["last_campaign"],
        attribution_evidence = json.dumps(attr["evidence"], default=str) if attr["evidence"] else None,
    )

    try:
        db.add(row)
        db.commit()
        log.info(
            "track/purchase-confirmed: stored — visitor=%s order=%s shop=%s first=%s last=%s",
            visitor_id, shopify_order_id, payload.shop_domain,
            attr["first_source"], attr["last_source"],
        )
        return {"status": "ok"}

    except IntegrityError:
        db.rollback()
        log.info(
            "track/purchase-confirmed: duplicate skipped — order_id=%s shop=%s",
            shopify_order_id, payload.shop_domain,
        )
        return {"status": "duplicate"}
