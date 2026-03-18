from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/top-pages")
def top_pages(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    query = text("""
        SELECT
            url,
            COUNT(*) FILTER (WHERE event_type='page_view') AS views,
            COUNT(DISTINCT visitor_id) AS visitors,
            AVG(COALESCE(dwell_seconds,0)) FILTER (WHERE event_type='page_leave') AS avg_dwell
        FROM events
        WHERE shop_domain = :shop_domain
        GROUP BY url
        ORDER BY views DESC
        LIMIT 10
    """)
    with engine.begin() as conn:
        rows = conn.execute(query, {"shop_domain": shop}).mappings().all()

    return {"pages": [dict(r) for r in rows]}
