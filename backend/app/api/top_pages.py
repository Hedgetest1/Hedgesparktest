from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


class TopPageRow(BaseModel):
    """One page in the top-pages ranking."""
    url: str
    views: int
    visitors: int
    avg_dwell: float | None = None


class TopPagesResponse(BaseModel):
    """GET /analytics/top-pages — top 10 most-viewed pages."""
    pages: list[TopPageRow]


@router.get(
    "/top-pages",
    response_model=TopPagesResponse,
    response_model_exclude_none=False,
)
def top_pages(
    shop: str = Depends(require_merchant_session),
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
