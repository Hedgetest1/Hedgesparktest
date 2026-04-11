"""
GET /analytics/clicks — Click Insight.

Returns the top 10 most-clicked pages/elements ranked by click count.
Sourced directly from events where event_type = 'click'.

No heatmap rendering — just a ranked list with URL and click count.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ClickInsightRow(BaseModel):
    """One URL in the click ranking."""
    url: str
    clicks: int


class ClickInsightsResponse(BaseModel):
    """GET /analytics/clicks — top 10 most-clicked URLs."""
    clicks: list[ClickInsightRow]


@router.get(
    "/clicks",
    response_model=ClickInsightsResponse,
    response_model_exclude_none=False,
)
def click_insights(
    shop: str = Depends(require_merchant_session),
):
    query = text("""
        SELECT
            COALESCE(url, 'unknown') AS url,
            COUNT(*)                 AS clicks
        FROM events
        WHERE event_type  = 'click'
          AND shop_domain = :shop_domain
        GROUP BY url
        ORDER BY clicks DESC
        LIMIT 10
    """)

    with engine.begin() as conn:
        rows = conn.execute(query, {"shop_domain": shop}).mappings().all()

    return {
        "clicks": [
            {"url": r["url"], "clicks": int(r["clicks"] or 0)}
            for r in rows
        ]
    }
