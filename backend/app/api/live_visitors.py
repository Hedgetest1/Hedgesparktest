"""
live_visitors.py — Real-time visitor pulse endpoint.

GET /live/visitors
    Returns the top 20 most recent visitors with intent scoring.

    Recency window: 15 minutes.  Only visitors with events in the last
    15 minutes appear.  This is the "live" contract — no historical ghosts.

    Cached in Redis for 10 seconds to prevent per-tab polling (5s default)
    from hammering the DB.  With 10s cache + 15s polling, the merchant sees
    data that is at most 25 seconds old — perceptually live.

Auth: require_merchant_session (session cookie or legacy API key).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session

router = APIRouter(prefix="/live", tags=["live"])

# Recency window: 15 minutes in epoch milliseconds
_RECENCY_MS = 15 * 60 * 1000


@router.get("/visitors")
def live_visitors(
    shop: str = Depends(require_merchant_session),
):
    # Check Redis cache first
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:live_visitors:{shop}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    query = text("""
        WITH recent_events AS (
            SELECT
                visitor_id,
                event_type,
                timestamp,
                dwell_seconds,
                max_scroll_depth,
                url
            FROM events
            WHERE shop_domain = :shop_domain
              AND timestamp > (EXTRACT(EPOCH FROM NOW()) * 1000 - :recency_ms)
        ),
        latest AS (
            SELECT
                visitor_id,
                MAX(timestamp) AS last_ts,
                MAX(COALESCE(dwell_seconds, 0)) AS dwell_seconds,
                MAX(COALESCE(max_scroll_depth, 0)) AS max_scroll_depth
            FROM recent_events
            GROUP BY visitor_id
        ),
        clicks AS (
            SELECT visitor_id, COUNT(*) AS click_count
            FROM recent_events
            WHERE event_type = 'click'
            GROUP BY visitor_id
        ),
        pages AS (
            SELECT DISTINCT ON (e.visitor_id)
                e.visitor_id, e.url
            FROM recent_events e
            JOIN latest l ON e.visitor_id = l.visitor_id
                         AND e.timestamp = l.last_ts
            ORDER BY e.visitor_id, e.timestamp DESC
        )
        SELECT
            l.visitor_id,
            COALESCE(p.url, '') AS url,
            COALESCE(l.dwell_seconds, 0) AS dwell_seconds,
            COALESCE(l.max_scroll_depth, 0) AS max_scroll_depth,
            COALESCE(c.click_count, 0) AS click_count,
            CASE
                WHEN COALESCE(l.max_scroll_depth, 0) >= 80
                     AND COALESCE(l.dwell_seconds, 0) >= 20
                     AND COALESCE(c.click_count, 0) >= 1
                THEN 'HOT'
                WHEN COALESCE(l.max_scroll_depth, 0) >= 40
                     OR COALESCE(l.dwell_seconds, 0) >= 10
                THEN 'WARM'
                ELSE 'COLD'
            END AS intent_level
        FROM latest l
        LEFT JOIN clicks c ON c.visitor_id = l.visitor_id
        LEFT JOIN pages p ON p.visitor_id = l.visitor_id
        ORDER BY l.last_ts DESC
        LIMIT 20
    """)

    with engine.begin() as conn:
        rows = conn.execute(query, {
            "shop_domain": shop,
            "recency_ms": _RECENCY_MS,
        }).mappings().all()

    result = {"visitors": [dict(r) for r in rows]}
    cache_set(cache_key, result, 10)  # 10 second TTL
    return result
