from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/alerts")
def alerts(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    query = text("""
        WITH visitor_stats AS (
            SELECT
                visitor_id,
                MAX(url) AS url,
                MAX(COALESCE(dwell_seconds,0)) AS dwell,
                MAX(COALESCE(max_scroll_depth,0)) AS scroll,
                COUNT(*) FILTER (WHERE event_type='click') AS clicks
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY visitor_id
        ),
        hot_visitors AS (
            SELECT COUNT(*) AS hot_count
            FROM visitor_stats
            WHERE scroll >= 70 AND dwell >= 20 AND clicks >= 1
        ),
        checkout_activity AS (
            SELECT COUNT(*) AS checkout_views
            FROM events
            WHERE shop_domain = :shop_domain
              AND url LIKE '%checkout%'
        ),
        product_activity AS (
            SELECT COUNT(*) AS product_views
            FROM events
            WHERE shop_domain = :shop_domain
              AND (url LIKE '%product%' OR url LIKE '%test.html%')
        )
        SELECT
            (SELECT hot_count FROM hot_visitors) AS hot_visitors,
            (SELECT checkout_views FROM checkout_activity) AS checkout_views,
            (SELECT product_views FROM product_activity) AS product_views
    """)
    with engine.begin() as conn:
        row = conn.execute(query, {"shop_domain": shop}).mappings().first()

    result = []

    if row["hot_visitors"] >= 1:
        result.append({
            "type": "HOT_TRAFFIC_CLUSTER",
            "message": f"{row['hot_visitors']} high-intent visitors browsing now",
            "priority": "HIGH",
        })

    if row["checkout_views"] >= 1:
        result.append({
            "type": "CHECKOUT_ACTIVITY",
            "message": f"{row['checkout_views']} checkout page views detected",
            "priority": "MEDIUM",
        })

    if row["product_views"] >= 1:
        result.append({
            "type": "PRODUCT_INTEREST",
            "message": f"{row['product_views']} product page views happening",
            "priority": "LOW",
        })

    return {"alerts": result}
