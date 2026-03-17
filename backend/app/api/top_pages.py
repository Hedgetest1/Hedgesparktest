from fastapi import APIRouter
from sqlalchemy import text
from app.core.database import engine

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/top-pages")
def top_pages():
    query = text("""
        SELECT
            url,
            COUNT(*) FILTER (WHERE event_type='page_view') AS views,
            COUNT(DISTINCT visitor_id) AS visitors,
            AVG(COALESCE(dwell_seconds,0)) FILTER (WHERE event_type='page_leave') AS avg_dwell
        FROM events
        GROUP BY url
        ORDER BY views DESC
        LIMIT 10
    """)
    with engine.begin() as conn:
        rows = conn.execute(query).mappings().all()

    return {"pages":[dict(r) for r in rows]}
