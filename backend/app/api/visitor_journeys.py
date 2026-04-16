"""
visitor_journeys.py — Real visitor journey samples for dashboard drawer.

KILLER INNOVATION (π6): merchants click on a source in the MTA card and
see actual customer journeys that ended in a purchase through that
source — with timestamps, touchpoint sequence, and the conversion point.
This is visible proof that the attribution numbers are real and not
a black box.

No competitor can show this without our joined data model
(events → visitor_purchase_sessions → shop_orders).

Endpoint
--------
GET /pro/visitor-journeys?source={source}&limit=5&window_days=30
    Returns up to N real journeys that touched the given source and
    ended in a purchase.

Each journey: visitor_id (hashed), purchase_at, revenue, list of
touches (source, campaign, days_before_purchase, is_first, is_last).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["visitor_journeys"])




class JourneyTouch(BaseModel):
    source: str
    campaign: str | None
    hours_before_purchase: float
    is_first: bool
    is_last: bool


class VisitorJourney(BaseModel):
    visitor_hash: str
    purchase_at: str
    revenue_eur: float
    touch_count: int
    window_hours: float
    touches: list[JourneyTouch]


class VisitorJourneysResponse(BaseModel):
    shop_domain: str
    source_filter: str | None
    window_days: int
    total_found: int
    journeys: list[VisitorJourney]
    generated_at: str


def _hash_visitor_id(vid: str) -> str:
    """One-way hash so the UI never shows raw visitor IDs (GDPR-safe)."""
    return "V-" + hashlib.sha1(vid.encode("utf-8")).hexdigest()[:8].upper()


@router.get("/visitor-journeys", response_model=VisitorJourneysResponse)
def get_visitor_journeys(
    source: str | None = Query(None, description="Filter to journeys that touched this source"),
    window_days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=window_days)

    # Find candidate orders first — if a source filter is given, only
    # pull journeys whose touch set includes that source.
    try:
        if source:
            order_rows = db.execute(
                sql_text(
                    """
                    SELECT DISTINCT vps.visitor_id, vps.shopify_order_id, vps.confirmed_at,
                           so.total_price
                    FROM visitor_purchase_sessions vps
                    JOIN shop_orders so
                      ON so.shop_domain = vps.shop_domain
                     AND so.shopify_order_id = vps.shopify_order_id
                    WHERE vps.shop_domain = :shop
                      AND vps.confirmed_at >= :cutoff
                      AND vps.visitor_id IN (
                          SELECT DISTINCT e.visitor_id FROM events e
                          WHERE e.shop_domain = :shop
                            AND e.source_type = :src
                      )
                    ORDER BY vps.confirmed_at DESC
                    LIMIT :lim
                    """
                ),
                {"shop": shop, "cutoff": cutoff, "src": source, "lim": limit * 3},
            ).fetchall()
        else:
            order_rows = db.execute(
                sql_text(
                    """
                    SELECT vps.visitor_id, vps.shopify_order_id, vps.confirmed_at,
                           so.total_price
                    FROM visitor_purchase_sessions vps
                    JOIN shop_orders so
                      ON so.shop_domain = vps.shop_domain
                     AND so.shopify_order_id = vps.shopify_order_id
                    WHERE vps.shop_domain = :shop
                      AND vps.confirmed_at >= :cutoff
                    ORDER BY vps.confirmed_at DESC
                    LIMIT :lim
                    """
                ),
                {"shop": shop, "cutoff": cutoff, "lim": limit * 3},
            ).fetchall()
    except Exception as exc:
        log.warning("visitor_journeys: order query failed: %s", exc)
        order_rows = []

    journeys: list[VisitorJourney] = []

    # Single batch query for touches across all visitors — avoids an N+1
    # that fires one SELECT per visitor on every call to this endpoint.
    # At limit=50 that's 50× reduction to 1× regardless of request size.
    candidate_visitors = [
        (vid, oid, cat, tp)
        for (vid, oid, cat, tp) in order_rows
        if vid and cat
    ][: limit * 3]

    if candidate_visitors:
        visitor_ids = [v[0] for v in candidate_visitors]
        try:
            touch_rows = db.execute(
                sql_text(
                    """
                    SELECT visitor_id, source_type, utm_campaign, timestamp
                    FROM events
                    WHERE shop_domain = :shop
                      AND visitor_id = ANY(:vids)
                      AND source_type IS NOT NULL
                    ORDER BY visitor_id, timestamp ASC
                    """
                ),
                {"shop": shop, "vids": visitor_ids},
            ).fetchall()
        except Exception as exc:
            log.warning("visitor_journeys: batch touch query failed: %s", exc)
            touch_rows = []

        # Group touches by visitor_id (caller sorted by the DB)
        touches_by_visitor: dict[str, list[tuple]] = {}
        for vid, source_type, campaign, ts_ms in touch_rows:
            if not vid:
                continue
            touches_by_visitor.setdefault(vid, []).append(
                (source_type, campaign, ts_ms)
            )
    else:
        touches_by_visitor = {}

    for visitor_id, order_id, confirmed_at, total_price in candidate_visitors:
        if len(journeys) >= limit:
            break
        touches = touches_by_visitor.get(visitor_id, [])[:50]  # per-visitor cap

        # Filter + dedupe + convert
        purchase_ms = int(confirmed_at.timestamp() * 1000)
        parsed: list[tuple[str, str | None, datetime]] = []
        last_source = None
        for row in touches:
            source_type, campaign, ts_ms = row
            if ts_ms is None or source_type is None:
                continue
            try:
                ts_ms_int = int(ts_ms)
                if ts_ms_int > purchase_ms:
                    continue
                touch_ts = datetime.utcfromtimestamp(ts_ms_int / 1000.0)
            except Exception:
                continue
            if source_type == last_source:
                continue
            parsed.append((source_type, campaign, touch_ts))
            last_source = source_type

        if not parsed:
            continue

        # Make sure the journey actually includes the filtered source
        if source and not any(s == source for s, _, _ in parsed):
            continue

        first_idx = 0
        last_idx = len(parsed) - 1

        touch_list = []
        for i, (src, camp, ts) in enumerate(parsed):
            hours_before = max(
                0.0, (confirmed_at - ts).total_seconds() / 3600.0
            )
            touch_list.append(
                JourneyTouch(
                    source=src,
                    campaign=camp,
                    hours_before_purchase=round(hours_before, 1),
                    is_first=(i == first_idx),
                    is_last=(i == last_idx),
                )
            )

        first_ts = parsed[0][2]
        window_hours = round((confirmed_at - first_ts).total_seconds() / 3600.0, 1)

        journeys.append(
            VisitorJourney(
                visitor_hash=_hash_visitor_id(visitor_id),
                purchase_at=confirmed_at.isoformat(),
                revenue_eur=round(float(total_price or 0), 2),
                touch_count=len(touch_list),
                window_hours=window_hours,
                touches=touch_list,
            )
        )

    return VisitorJourneysResponse(
        shop_domain=shop,
        source_filter=source,
        window_days=window_days,
        total_found=len(journeys),
        journeys=journeys,
        generated_at=now.isoformat(),
    )
