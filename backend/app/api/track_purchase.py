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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.client_ip import extract_client_ip
from app.core.redis_client import _client as _redis_client
from app.core.silent_fallback import record_silent_return
from app.models.merchant import Merchant
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.shopify_auth import is_valid_shop_domain

log = logging.getLogger(__name__)

router = APIRouter(tags=["attribution"])


# Security guards for unauthenticated POST /track/purchase-confirmed.
# Born 2026-05-11 Sprint A security audit (CRITICAL C1): anonymous
# callers were able to poison cross-tenant attribution for any
# *.myshopify.com domain. Defense in depth:
#   1. is_valid_shop_domain (regex format) — was already present
#   2. _is_known_shop (shop must be installed)
#   3. Per-IP+shop rate limit (60/60s)
#   4. Visitor session plausibility (visitor must have ≥1 event for
#      this shop in the last 90d — eliminates pure forgery)
_PER_SHOP_RATE_PER_60S = 60
_VISITOR_PLAUSIBILITY_DAYS = 90


def _is_known_shop(db: Session, shop_domain: str) -> bool:
    """Check if shop_domain belongs to a known installed merchant.

    Mirror of `app.api.track._is_known_shop` — Redis-cached 5 min.
    Primary cross-tenant abuse guard."""
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:known_shop:{shop_domain}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    exists = (
        db.query(Merchant)
        .filter(
            Merchant.shop_domain == shop_domain,
            Merchant.install_status == "active",
        )
        .first()
    ) is not None
    cache_set(cache_key, exists, 300)
    return exists


def _check_per_shop_rate(request: Request, shop_domain: str) -> bool:
    """Per-IP + per-shop rate limit: max 60 attribution events / 60s.

    Mirror of `app.api.track._check_per_shop_rate`. Fail-open on Redis
    outage (record_silent_return for audit telemetry). Without this an
    attacker who knows a valid shop_domain + visitor_id can flood
    attribution overwrites."""
    try:
        client = _redis_client()
        if client is None:
            record_silent_return("track_purchase.per_shop_rate")
            return True
        ip = extract_client_ip(request)
        key = f"hs:rl:track_purchase:{ip}:{shop_domain}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        return count <= _PER_SHOP_RATE_PER_60S
    except Exception as exc:
        log.warning("track_purchase: rate-limit check failed: %s", exc)
        return True  # fail open per existing tracker doctrine


def _visitor_has_events_for_shop(
    db: Session, shop_domain: str, visitor_id: str,
) -> bool:
    """Plausibility: a real attribution requires the visitor to have
    tracked at least one event for this shop within the lookback window.

    Eliminates pure forgery: an attacker generating random visitor_ids
    would have ZERO events → reject. Legitimate browsers that completed
    a real checkout flow have at minimum a `page_view` event from the
    storefront tracker before the Order Status page POST.

    Implementation note: `events` is RANGE-partitioned on `timestamp`
    (bigint epoch ms — NOT a SQL `ts` column). We compute the cutoff
    in Python and pass as a bound int, which lets PG's partition
    pruner skip old partitions cleanly. Composite index
    `(shop_domain, visitor_id, timestamp)` covers the WHERE; LIMIT 1
    exits early."""
    try:
        from datetime import datetime, timezone, timedelta
        cutoff_ms = int((
            datetime.now(timezone.utc)
            - timedelta(days=_VISITOR_PLAUSIBILITY_DAYS)
        ).timestamp() * 1000)
        row = db.execute(text("""
            SELECT 1
              FROM events
             WHERE shop_domain = :s
               AND visitor_id = :v
               AND timestamp >= :cutoff_ms
             LIMIT 1
        """), {
            "s": shop_domain,
            "v": visitor_id,
            "cutoff_ms": cutoff_ms,
        }).fetchone()
        return row is not None
    except Exception as exc:
        # On query failure (transient DB issue), fail-open: we don't
        # want a single bad query to block all legitimate attribution.
        # The other 3 guards (format + known-shop + rate-limit) still
        # cover the threat surface.
        log.warning(
            "track_purchase: visitor plausibility check failed: %s", exc,
        )
        return True




class PurchaseAttributionPayload(BaseModel):
    """
    Payload from spark-attribution.js on the Shopify thank-you page.

    All fields are required — the script only fires when all three identity
    anchors (shop_domain, visitor_id, shopify_order_id) are resolvable.
    """
    shop_domain:       str = Field(..., max_length=255)
    visitor_id:        str = Field(..., max_length=128)
    shopify_order_id:  str = Field(..., max_length=64)
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
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receive and persist a visitor-to-order attribution event.

    Called by spark-attribution.js from the Shopify Order Status page.
    Stores one row in visitor_purchase_sessions per unique shopify_order_id.
    Resolves first-touch and last-touch attribution at conversion time.

    Security (born 2026-05-11 Sprint A audit C1 — cross-tenant
    attribution forgery): the endpoint is anonymous by design (called
    from merchant browser, no auth header). Defense in depth:
      1. shop_domain format (`*.myshopify.com` regex)
      2. shop must be an installed Merchant (`_is_known_shop`)
      3. Per-IP+shop rate limit (60/60s)
      4. visitor_id must have ≥1 event for shop in the last 90d
         (visitor plausibility — eliminates pure forgery)

    Returns
    -------
    {"status": "ok"}        — new attribution stored
    {"status": "duplicate"} — shopify_order_id already attributed; row unchanged

    HTTP 400 on:
    - Invalid shop_domain format (must be *.myshopify.com)
    - Empty visitor_id or shopify_order_id
    HTTP 404 on:
    - shop_domain is not a known installed merchant
    HTTP 422 on:
    - visitor_id has no tracked events for this shop (likely forgery)
    HTTP 429 on:
    - per-IP+shop rate exceeded (60/60s)
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

    # Security guard #2: shop must be installed (Sprint A C1)
    if not _is_known_shop(db, payload.shop_domain):
        log.warning(
            "track/purchase-confirmed: unknown shop=%s — rejected",
            payload.shop_domain,
        )
        raise HTTPException(
            status_code=404,
            detail="Shop is not an installed HedgeSpark merchant.",
        )

    # Security guard #3: per-IP+shop rate limit (Sprint A C1)
    if not _check_per_shop_rate(request, payload.shop_domain):
        log.warning(
            "track/purchase-confirmed: rate-limited ip+shop=%s — rejected",
            payload.shop_domain,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many attribution events for this shop. Retry shortly.",
        )

    # Security guard #4: visitor must have tracked events for this shop
    # (Sprint A C1 — visitor plausibility, eliminates pure forgery)
    if not _visitor_has_events_for_shop(db, payload.shop_domain, visitor_id):
        log.warning(
            "track/purchase-confirmed: visitor=%s has no events for shop=%s "
            "in last %dd — rejected (likely forgery)",
            visitor_id, payload.shop_domain, _VISITOR_PLAUSIBILITY_DAYS,
        )
        raise HTTPException(
            status_code=422,
            detail="visitor_id has no tracked events for this shop.",
        )

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
