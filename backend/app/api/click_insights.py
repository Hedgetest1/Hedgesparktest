"""
GET /analytics/clicks — Click Insight.

Returns the top 10 most-clicked pages/elements ranked by click count.
Sourced directly from events where event_type = 'click'.

No heatmap rendering — just a ranked list with URL and click count.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/clicks")
def click_insights(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
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
