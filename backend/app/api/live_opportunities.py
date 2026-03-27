from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/live-opportunities")
def live_opportunities(
    shop: str = Depends(require_merchant_session),
):
    query = text("""
        WITH page_views AS (
            SELECT
                url,
                COUNT(*) FILTER (WHERE event_type = 'page_view') AS views,
                COUNT(DISTINCT visitor_id) AS visitors
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY url
        ),
        page_leaves AS (
            SELECT
                url,
                AVG(COALESCE(dwell_seconds, 0)) FILTER (WHERE event_type = 'page_leave') AS avg_dwell,
                AVG(COALESCE(max_scroll_depth, 0)) FILTER (WHERE event_type = 'page_leave') AS avg_scroll
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY url
        ),
        clicks AS (
            SELECT
                url,
                COUNT(*) FILTER (WHERE event_type = 'click') AS clicks
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY url
        )
        SELECT
            pv.url,
            COALESCE(pv.views, 0) AS views,
            COALESCE(pv.visitors, 0) AS visitors,
            COALESCE(pl.avg_dwell, 0) AS avg_dwell,
            COALESCE(pl.avg_scroll, 0) AS avg_scroll,
            COALESCE(c.clicks, 0) AS clicks,
            CASE
                WHEN COALESCE(pv.views, 0) >= 3
                     AND COALESCE(pl.avg_scroll, 0) >= 70
                     AND COALESCE(pl.avg_dwell, 0) >= 20
                     AND COALESCE(c.clicks, 0) >= 1
                THEN 'HIGH_INTENT_PAGE'
                WHEN COALESCE(pv.views, 0) >= 2
                     AND COALESCE(pl.avg_scroll, 0) >= 50
                THEN 'ENGAGED_PAGE'
                ELSE 'LOW_SIGNAL'
            END AS signal_type,
            CASE
                WHEN COALESCE(pv.views, 0) >= 3
                     AND COALESCE(pl.avg_scroll, 0) >= 70
                     AND COALESCE(pl.avg_dwell, 0) >= 20
                     AND COALESCE(c.clicks, 0) >= 1
                THEN 'Push urgency or checkout nudge'
                WHEN COALESCE(pv.views, 0) >= 2
                     AND COALESCE(pl.avg_scroll, 0) >= 50
                THEN 'Highlight CTA and pricing'
                ELSE 'Collect more data'
            END AS recommended_action,
            CASE
                WHEN COALESCE(pv.views, 0) >= 3
                     AND COALESCE(pl.avg_scroll, 0) >= 70
                     AND COALESCE(pl.avg_dwell, 0) >= 20
                     AND COALESCE(c.clicks, 0) >= 1
                THEN 90
                WHEN COALESCE(pv.views, 0) >= 2
                     AND COALESCE(pl.avg_scroll, 0) >= 50
                THEN 70
                ELSE 40
            END AS priority_score,
            CASE
                WHEN COALESCE(pv.views, 0) >= 3
                     AND COALESCE(pl.avg_scroll, 0) >= 70
                     AND COALESCE(pl.avg_dwell, 0) >= 20
                     AND COALESCE(c.clicks, 0) >= 1
                THEN 'Visitors are highly engaged but may need a final conversion push.'
                WHEN COALESCE(pv.views, 0) >= 2
                     AND COALESCE(pl.avg_scroll, 0) >= 50
                THEN 'Page shows solid engagement and deserves optimization.'
                ELSE 'Not enough live signal yet.'
            END AS explanation
        FROM page_views pv
        LEFT JOIN page_leaves pl ON pl.url = pv.url
        LEFT JOIN clicks c ON c.url = pv.url
        ORDER BY priority_score DESC, views DESC
        LIMIT 10
    """)
    with engine.begin() as conn:
        rows = conn.execute(query, {"shop_domain": shop}).mappings().all()

    return {"opportunities": [dict(r) for r in rows]}
