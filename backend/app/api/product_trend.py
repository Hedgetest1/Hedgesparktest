"""
product_trend.py — GET /products/trend

Returns a 7-day view timeseries per product, ordered by total views descending.

Each item contains:
    product_url         str
    last_7_days_views   list[int]  — 7 integers, index 0 = 7 days ago, index 6 = today
    total_views         int        — sum across the 7-day window

Only product page URLs (containing /products/) are included.
Events are grouped by calendar date derived from the epoch-millisecond timestamp column.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_merchant_session
from app.schemas.product_trend import ProductTrendResponse, ProductTrendRow

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/trend", response_model=ProductTrendResponse)
def get_product_trend(
    shop: str = Depends(require_merchant_session),
):
    """
    Return the 7-day view timeseries for each product in the shop.

    Days are calendar dates in UTC.  The array is always length 7:
        index 0 → today - 6  (oldest)
        index 6 → today      (most recent)
    Missing days are filled with 0.
    """
    # Build the reference date list (UTC): today and the 6 days before it
    today = date.today()
    day_labels: list[date] = [today - timedelta(days=i) for i in range(6, -1, -1)]

    query = text(
        """
        WITH daily AS (
            SELECT
                url                                                        AS product_url,
                DATE(TO_TIMESTAMP(timestamp / 1000.0))                     AS event_day,
                COUNT(*)                                                    AS views
            FROM events
            WHERE shop_domain = :shop_domain
              AND url LIKE '%/products/%'
              AND timestamp >= EXTRACT(EPOCH FROM (NOW() - INTERVAL '7 days')) * 1000
            GROUP BY url, DATE(TO_TIMESTAMP(timestamp / 1000.0))
        )
        SELECT
            product_url,
            SUM(views)  AS total_views,
            -- one column per calendar day, oldest → newest
            COALESCE(SUM(views) FILTER (WHERE event_day = :d0), 0) AS d0,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d1), 0) AS d1,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d2), 0) AS d2,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d3), 0) AS d3,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d4), 0) AS d4,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d5), 0) AS d5,
            COALESCE(SUM(views) FILTER (WHERE event_day = :d6), 0) AS d6
        FROM daily
        GROUP BY product_url
        ORDER BY total_views DESC
        LIMIT 20
        """
    )

    params = {
        "shop_domain": shop,
        "d0": day_labels[0],
        "d1": day_labels[1],
        "d2": day_labels[2],
        "d3": day_labels[3],
        "d4": day_labels[4],
        "d5": day_labels[5],
        "d6": day_labels[6],
    }

    with engine.begin() as conn:
        result = conn.execute(query, params)
        rows = result.fetchall()

    products: list[ProductTrendRow] = [
        ProductTrendRow(
            product_url=row.product_url,
            last_7_days_views=[
                int(row.d0),
                int(row.d1),
                int(row.d2),
                int(row.d3),
                int(row.d4),
                int(row.d5),
                int(row.d6),
            ],
            total_views=int(row.total_views),
        )
        for row in rows
    ]

    return ProductTrendResponse(
        shop_domain=shop,
        count=len(products),
        products=products,
    )
