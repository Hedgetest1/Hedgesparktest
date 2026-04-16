"""
orders.py — Real order/revenue data endpoints.

GET /orders/summary
    Returns real revenue metrics from shop_orders for the authenticated merchant.
    Windows: 7d, 30d.  Includes top products by revenue.

GET /orders/product-conversions?days=7
    Per-product conversion funnel: views → add_to_cart → purchases → revenue.
    Joins: events (views, ATC) → visitor_purchase_sessions (attribution bridge)
           → shop_orders (real revenue via line_items JSONB).
    All numbers are real — no estimates.  Missing data → empty, not fake.

Auth: require_merchant_session (session cookie).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


# ---------------------------------------------------------------------------
# Response models for /orders/forecast/pro — Forecast cassettone source.
# ---------------------------------------------------------------------------


class ForecastDailyPoint(BaseModel):
    """One day in the historical revenue series."""
    day: str
    revenue: float
    orders: int


class ForecastHistoryBlock(BaseModel):
    """Historical revenue series used as the forecast training window."""
    days_available: int
    days_with_revenue: int
    daily_series: list[ForecastDailyPoint]
    total_revenue: float
    avg_daily_revenue: float


class ForecastWindowBlock(BaseModel):
    """Projected revenue for a future window (7d or 30d)."""
    revenue: float
    revenue_low: float
    revenue_high: float
    avg_daily: float


class ForecastTrendBlock(BaseModel):
    """Linear trend classification from the history window."""
    direction: str
    slope_per_day: float
    weekly_change_pct: float


class RevenueForecastResponse(BaseModel):
    """GET /orders/forecast/pro — deterministic revenue forecast."""
    generated_at: str
    currency: str
    history: ForecastHistoryBlock
    forecast_7d: ForecastWindowBlock | None = None
    forecast_30d: ForecastWindowBlock | None = None
    trend: ForecastTrendBlock | None = None
    confidence: str | None = None
    confidence_reason: str
    seasonality_available: bool


# ---------------------------------------------------------------------------
# Response models for /orders/summary, /orders/daily-revenue, /orders/product-conversions
# ---------------------------------------------------------------------------


class OrdersWindowStats(BaseModel):
    """Per-window order stats (7d / 30d slice)."""
    order_count: int
    total_revenue: float
    avg_order_value: float


class TopProductByRevenue(BaseModel):
    """One row inside top_products_by_revenue."""
    product_title: str
    revenue: float
    units_sold: int


class OrdersSummaryResponse(BaseModel):
    """GET /orders/summary — real revenue summary from shop_orders."""
    has_orders: bool
    currency: str
    last_7d: OrdersWindowStats
    last_30d: OrdersWindowStats
    top_products_by_revenue: list[TopProductByRevenue]


class DailyRevenuePoint(BaseModel):
    """One day in the /orders/daily-revenue series."""
    day: str
    revenue: float
    orders: int


class DailyRevenueResponse(BaseModel):
    """GET /orders/daily-revenue — revenue per day for the last N days."""
    points: list[DailyRevenuePoint]
    currency: str
    days: int


class ProductConversionRow(BaseModel):
    """One row in the /orders/product-conversions funnel response."""
    product_url: str
    product_name: str
    views: int
    unique_viewers: int
    add_to_cart: int
    purchases: int
    units_sold: int
    revenue: float
    cvr: float = Field(..., ge=0.0)
    atc_rate: float = Field(..., ge=0.0)
    avg_order_value: float


class ProductConversionsResponse(BaseModel):
    """GET /orders/product-conversions — per-product conversion funnel."""
    products: list[ProductConversionRow]
    days: int
    currency: str
    has_data: bool


@router.get(
    "/summary",
    response_model=OrdersSummaryResponse,
    response_model_exclude_none=False,
)
def get_orders_summary(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Return real revenue summary from shop_orders.

    Returns empty/zero data gracefully when no orders exist.
    All numbers are real — no estimates, no fallbacks.
    """
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:orders_summary:{shop}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    # Resolve currency first — used in all revenue queries
    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop) or "USD"

    # 7-day and 30-day windows (filtered by shop's primary currency)
    result_7d = _query_window(db, shop, 7, currency=currency)
    result_30d = _query_window(db, shop, 30, currency=currency)
    top_products = _top_products_by_revenue(db, shop, 30)

    summary = {
        "has_orders": result_30d["order_count"] > 0,
        "currency": currency,
        "last_7d": result_7d,
        "last_30d": result_30d,
        "top_products_by_revenue": top_products,
    }
    cache_set(cache_key, summary, 120)  # 2 min TTL
    return summary


def _query_window(db: Session, shop: str, days: int, currency: str | None = None) -> dict:
    try:
        row = db.execute(
            text("""
                SELECT
                    COUNT(*)::int                        AS order_count,
                    COALESCE(SUM(total_price), 0) AS total_revenue,
                    COALESCE(AVG(total_price), 0) AS avg_order_value
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND (:currency IS NULL OR currency = :currency)
            """),
            {"shop": shop, "days": days, "currency": currency},
        ).fetchone()
        if row is None:
            return {"order_count": 0, "total_revenue": 0, "avg_order_value": 0}
        return {
            "order_count": int(row[0] or 0),
            "total_revenue": round(float(row[1] or 0), 2),
            "avg_order_value": round(float(row[2] or 0), 2),
        }
    except Exception as exc:
        log.warning("orders._query_window: shop=%s days=%d: %s", shop, days, exc)
        return {"order_count": 0, "total_revenue": 0, "avg_order_value": 0}


def _top_products_by_revenue(db: Session, shop: str, days: int, limit: int = 5) -> list:
    """Top products by revenue from line_items JSONB."""
    try:
        rows = db.execute(
            text("""
                SELECT
                    item->>'title'                                AS product_title,
                    SUM((item->>'price')::numeric * (item->>'quantity')::int) AS revenue,
                    SUM((item->>'quantity')::int)                 AS units_sold
                FROM shop_orders,
                     jsonb_array_elements(line_items) AS item
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND item->>'title' IS NOT NULL
                  AND item->>'price' IS NOT NULL
                  AND item->>'quantity' IS NOT NULL
                GROUP BY item->>'title'
                ORDER BY revenue DESC
                LIMIT :lim
            """),
            {"shop": shop, "days": days, "lim": limit},
        ).fetchall()
        return [
            {
                "product_title": r[0],
                "revenue": round(float(r[1] or 0), 2),
                "units_sold": int(r[2] or 0),
            }
            for r in rows
        ]
    except Exception as exc:
        log.warning("orders._top_products_by_revenue: %s", exc)
        return []


# ---------------------------------------------------------------------------
# GET /orders/daily-revenue — Revenue per day for last N days
# ---------------------------------------------------------------------------

@router.get(
    "/daily-revenue",
    response_model=DailyRevenueResponse,
    response_model_exclude_none=False,
)
def get_daily_revenue(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=1, le=30),
):
    """
    Return revenue per day for the last N days (default 7).

    Always returns exactly `days` entries (zero-filled for days with no orders).
    Used by the RevenueHero trend chart — lightweight, cached 2 min.
    """
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:daily_revenue:{shop}:{days}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from app.services.revenue_metrics import get_shop_currency, get_shop_timezone
    currency = get_shop_currency(db, shop) or "USD"
    tz = get_shop_timezone(db, shop)

    try:
        rows = db.execute(
            text("""
                SELECT
                    d.day::date                              AS day,
                    COALESCE(SUM(so.total_price), 0)  AS revenue,
                    COUNT(so.id)::int                        AS orders
                FROM generate_series(
                    (CURRENT_DATE - make_interval(days => :days - 1)),
                    CURRENT_DATE,
                    '1 day'::interval
                ) AS d(day)
                LEFT JOIN shop_orders so
                    ON so.shop_domain = :shop
                   AND (so.created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date = d.day::date
                   AND so.currency = :currency
                GROUP BY d.day
                ORDER BY d.day ASC
            """),
            {"shop": shop, "days": days, "tz": tz, "currency": currency},
        ).fetchall()

        points = [
            {
                "day": str(r[0]),
                "revenue": round(float(r[1] or 0), 2),
                "orders": int(r[2] or 0),
            }
            for r in rows
        ]
    except Exception as exc:
        log.warning("orders.daily_revenue: shop=%s: %s", shop, exc)
        points = []

    result = {"points": points, "currency": currency, "days": days}
    cache_set(cache_key, result, 120)
    return result


# ---------------------------------------------------------------------------
# GET /orders/product-conversions — Per-product conversion funnel
# ---------------------------------------------------------------------------

@router.get(
    "/product-conversions",
    response_model=ProductConversionsResponse,
    response_model_exclude_none=False,
)
def get_product_conversions(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
):
    """
    Per-product conversion funnel: views → add_to_cart → purchases → revenue.

    Join strategy (avoids double counting):
    1. product_views CTE: COUNT DISTINCT visitor views per product_url from events.
       Only counts page_view + product_view events.  Time-bounded by `days`.
    2. atc CTE: COUNT DISTINCT visitors with add_to_cart per product_url.
    3. purchases CTE: COUNT DISTINCT orders per product (via shop_orders.line_items).
       Uses line_items JSONB → extracts product_id.  Time-bounded by order.created_at.
    4. revenue CTE: SUM(price × quantity) per product from line_items.

    The link between events and orders goes through:
       events.product_url → contains /products/{handle}
       shop_orders.line_items → contains product_id (Shopify numeric)
       events.product_id → matches shop_orders line_items product_id

    For products where product_id is available (most), we join on product_id.
    Revenue/purchases come ONLY from real shop_orders — never estimated.
    Views/ATC come from events within the same time window.

    All numbers use the SAME time window.  No mixing of all-time vs N-day.
    """
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:product_conversions:{shop}:{days}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from app.services.revenue_metrics import get_shop_currency
    currency = get_shop_currency(db, shop) or "USD"

    try:
        rows = db.execute(
            text("""
                WITH
                -- Time boundaries.
                -- :days * 86400000 overflows int32 for days >= 25 (max int32 = 2,147,483,647).
                -- CAST(:days AS bigint) forces the multiplication into bigint space.
                -- Using CAST() instead of ::bigint to avoid SQLAlchemy bind-parameter
                -- confusion with Postgres :: cast operator.
                -- Bug discovered 2026-04-10: without the cast, days=30 crashed the query.
                cutoff_ms AS (
                    SELECT (EXTRACT(EPOCH FROM NOW()) * 1000 - (CAST(:days AS bigint) * 86400000))::bigint AS ts
                ),
                cutoff_dt AS (
                    SELECT NOW() - make_interval(days => :days) AS dt
                ),

                -- 1. Product views: unique visitors who viewed each product
                product_views AS (
                    SELECT
                        product_url,
                        COUNT(DISTINCT visitor_id) AS view_visitors,
                        COUNT(*)                   AS total_views
                    FROM events, cutoff_ms
                    WHERE shop_domain  = :shop
                      AND product_url  IS NOT NULL
                      AND event_type   IN ('page_view', 'product_view')
                      AND timestamp    > cutoff_ms.ts
                    GROUP BY product_url
                ),

                -- 2. Add-to-cart: unique visitors per product
                atc AS (
                    SELECT
                        product_url,
                        COUNT(DISTINCT visitor_id) AS atc_visitors
                    FROM events, cutoff_ms
                    WHERE shop_domain  = :shop
                      AND product_url  IS NOT NULL
                      AND event_type   = 'add_to_cart'
                      AND timestamp    > cutoff_ms.ts
                    GROUP BY product_url
                ),

                -- 3. Revenue + purchases from REAL orders (line_items JSONB)
                -- Each line item is an independent product sale.
                -- We use product_id for matching (Shopify numeric ID).
                order_products AS (
                    SELECT
                        item->>'product_id'                              AS product_id,
                        item->>'title'                                   AS product_title,
                        SUM((item->>'quantity')::int)                    AS units_sold,
                        SUM((item->>'price')::numeric
                            * (item->>'quantity')::int)                  AS revenue,
                        COUNT(DISTINCT so.shopify_order_id)              AS order_count
                    FROM shop_orders so, cutoff_dt,
                         jsonb_array_elements(so.line_items) AS item
                    WHERE so.shop_domain = :shop
                      AND so.created_at >= cutoff_dt.dt
                      AND item->>'product_id' IS NOT NULL
                      AND item->>'price'      IS NOT NULL
                      AND item->>'quantity'    IS NOT NULL
                    GROUP BY item->>'product_id', item->>'title'
                ),

                -- 4. Map product_id → product_url from events (most recent)
                -- This bridges order line_items (which have product_id) to
                -- events (which have product_url).
                pid_to_url AS (
                    SELECT DISTINCT ON (product_id)
                        product_id,
                        product_url
                    FROM events
                    WHERE shop_domain  = :shop
                      AND product_id   IS NOT NULL
                      AND product_url  IS NOT NULL
                    ORDER BY product_id, timestamp DESC
                )

                -- Final join: combine views + ATC + real purchases
                SELECT
                    pv.product_url,
                    COALESCE(op.product_title, pv.product_url)  AS product_name,
                    pv.total_views,
                    pv.view_visitors,
                    COALESCE(a.atc_visitors, 0)                 AS atc_visitors,
                    COALESCE(op.order_count, 0)                 AS purchases,
                    COALESCE(op.units_sold, 0)                  AS units_sold,
                    COALESCE(op.revenue, 0)                     AS revenue
                FROM product_views pv
                LEFT JOIN atc a
                    ON a.product_url = pv.product_url
                LEFT JOIN pid_to_url pu
                    ON pu.product_url = pv.product_url
                LEFT JOIN order_products op
                    ON op.product_id = pu.product_id
                ORDER BY COALESCE(op.revenue, 0) DESC, pv.total_views DESC
                LIMIT 20
            """),
            {"shop": shop, "days": days},
        ).fetchall()

    except Exception as exc:
        log.error("orders.product_conversions: shop=%s: %s", shop, exc)
        return {"products": [], "days": days, "currency": currency, "has_data": False}

    products = []
    for r in rows:
        total_views = int(r[2] or 0)
        view_visitors = int(r[3] or 0)
        atc_visitors = int(r[4] or 0)
        purchases = int(r[5] or 0)
        units_sold = int(r[6] or 0)
        revenue = round(float(r[7] or 0), 2)

        # CVR: purchases / unique visitors who viewed (not total views)
        cvr = round(purchases / view_visitors, 4) if view_visitors > 0 else 0.0
        atc_rate = round(atc_visitors / view_visitors, 4) if view_visitors > 0 else 0.0
        avg_order_value = round(revenue / purchases, 2) if purchases > 0 else 0.0

        products.append({
            "product_url": r[0],
            "product_name": r[1] or r[0],
            "views": total_views,
            "unique_viewers": view_visitors,
            "add_to_cart": atc_visitors,
            "purchases": purchases,
            "units_sold": units_sold,
            "revenue": revenue,
            "cvr": cvr,
            "atc_rate": atc_rate,
            "avg_order_value": avg_order_value,
        })

    result = {
        "products": products,
        "days": days,
        "currency": currency,
        "has_data": len(products) > 0,
    }
    cache_set(cache_key, result, 120)  # 2 min TTL
    return result


# ---------------------------------------------------------------------------
# GET /orders/forecast/pro — Revenue forecast (Pro only)
# ---------------------------------------------------------------------------

@router.get(
    "/forecast/pro",
    response_model=RevenueForecastResponse,
    response_model_exclude_none=False,
)
def get_revenue_forecast_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
    history_days: int = Query(default=90, ge=7, le=365),
):
    """
    Deterministic revenue forecast based on historical daily order revenue.

    Uses linear regression on daily revenue series + volatility for range.
    Supports optional day-of-week seasonality when 3+ weeks of data available.

    If insufficient history: returns None for forecast fields with honest reason.

    Returns:
        {
            "currency": str,
            "history": {
                "days_available": int,
                "days_with_revenue": int,
                "daily_series": [{"day": str, "revenue": float, "orders": int}, ...],
                "total_revenue": float,
                "avg_daily_revenue": float,
            },
            "forecast_7d": {
                "revenue": float,
                "revenue_low": float,
                "revenue_high": float,
                "avg_daily": float,
            } | null,
            "forecast_30d": { ... } | null,
            "trend": {
                "direction": "up" | "flat" | "down",
                "slope_per_day": float,
                "weekly_change_pct": float,
            } | null,
            "confidence": "high" | "medium" | "low" | null,
            "confidence_reason": str,
            "seasonality_available": bool,
        }
    """
    from app.services.revenue_forecast import get_revenue_forecast
    return get_revenue_forecast(db, shop, history_days=history_days)
