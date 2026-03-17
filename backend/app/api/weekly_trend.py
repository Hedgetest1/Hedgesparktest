from fastapi import APIRouter
from sqlalchemy import text
from app.core.database import engine

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/weekly-trend")
def weekly_trend():
    query = text("""
        WITH daily AS (
            SELECT
                DATE(TO_TIMESTAMP(timestamp / 1000.0)) AS day,
                COUNT(*) FILTER (WHERE event_type = 'page_view') AS page_views,
                COUNT(*) FILTER (WHERE event_type = 'click') AS clicks,
                COUNT(DISTINCT visitor_id) AS visitors,
                COUNT(DISTINCT CASE
                    WHEN COALESCE(max_scroll_depth,0) >= 70
                     AND COALESCE(dwell_seconds,0) >= 20
                    THEN visitor_id
                END) AS hot_visitors
            FROM events
            GROUP BY DATE(TO_TIMESTAMP(timestamp / 1000.0))
        )
        SELECT
            day::text AS day,
            COALESCE(visitors,0) AS visitors,
            COALESCE(page_views,0) AS page_views,
            COALESCE(clicks,0) AS clicks,
            COALESCE(hot_visitors,0) AS hot_visitors
        FROM daily
        ORDER BY day DESC
        LIMIT 7
    """)
    with engine.begin() as conn:
        rows = conn.execute(query).mappings().all()

    return {"trend": list(reversed([dict(r) for r in rows]))}
