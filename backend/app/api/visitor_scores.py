from fastapi import APIRouter
from sqlalchemy import text
from app.core.database import engine

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/visitor-scores")
def visitor_scores():
    query = text("""
        WITH visitor_stats AS (
            SELECT
                visitor_id,
                MAX(url) AS url,
                MAX(COALESCE(dwell_seconds,0)) AS dwell,
                MAX(COALESCE(max_scroll_depth,0)) AS scroll,
                COUNT(*) FILTER (WHERE event_type='click') AS clicks
            FROM events
            GROUP BY visitor_id
        )
        SELECT
            visitor_id,
            url,
            dwell,
            scroll,
            clicks,
            (
                LEAST(dwell,60)*0.6 +
                scroll*0.3 +
                clicks*10
            ) AS conversion_score
        FROM visitor_stats
        ORDER BY conversion_score DESC
        LIMIT 20
    """)
    with engine.begin() as conn:
        rows = conn.execute(query).mappings().all()

    return {"visitors":[dict(r) for r in rows]}
