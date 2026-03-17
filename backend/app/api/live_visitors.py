from fastapi import APIRouter
from sqlalchemy import text
from app.core.database import engine

router = APIRouter(prefix="/live", tags=["live"])

@router.get("/visitors")
def live_visitors():
    query = text("""
        WITH latest AS (
            SELECT
                visitor_id,
                MAX(id) AS last_id,
                MAX(timestamp) AS last_ts,
                MAX(COALESCE(dwell_seconds, 0)) AS dwell_seconds,
                MAX(COALESCE(max_scroll_depth, 0)) AS max_scroll_depth
            FROM events
            GROUP BY visitor_id
        ),
        clicks AS (
            SELECT visitor_id, COUNT(*) AS click_count
            FROM events
            WHERE event_type = 'click'
            GROUP BY visitor_id
        ),
        pages AS (
            SELECT e.visitor_id, e.url
            FROM events e
            JOIN latest l ON e.id = l.last_id
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
        rows = conn.execute(query).mappings().all()

    return {"visitors": [dict(r) for r in rows]}
