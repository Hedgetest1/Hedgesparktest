"""lite_extras.py — 4 base-analytics endpoints for Lite floor.

Born 2026-04-26 from `project_lite_features_audit_2026_04_25.md` Class B
(post-Class A unlock today). Each closes a documented competitor-parity
gap vs Shopify Free / Lifetimely Free / Better Reports / Peel.

Endpoints (all `require_merchant_session` — Lite-accessible):

  GET /analytics/device-breakdown
      Visitor sessions split mobile / desktop / tablet (events.device_type).
      Closes Shopify Free baseline expectation.

  GET /analytics/top-customers-ltv
      Top N customers ranked by lifetime spend (shop_orders aggregate
      by customer_email). Closes Lifetimely Free `Top customers` view.

  GET /analytics/abandonment-trend
      Daily cart-abandonment % over last 14 days
      (events: cart_added vs purchase). Closes Shopify Free trend.

  GET /analytics/first-vs-repeat-aov
      AOV comparison: first-purchase customers vs repeat customers.
      Closes Lifetimely Free `New vs returning value` tile.

All four are pure aggregations over data we already collect — no schema
changes, no webhook expansion. Pro/Scale tiers see same data; the
endpoints are tier-neutral by design (they're "table-stakes" analytics
that EVERY merchant deserves regardless of plan).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.core.redis_client import cache_get, cache_set
from app.services.revenue_metrics import get_shop_currency, get_shop_timezone

log = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

CACHE_TTL_S = 60


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DeviceSlice(BaseModel):
    device: str          # "mobile" | "desktop" | "tablet" | "unknown"
    sessions: int
    pct: float           # 0.0–100.0


class DeviceBreakdownResponse(BaseModel):
    days: int
    total_sessions: int
    has_data: bool
    slices: list[DeviceSlice]


class TopCustomer(BaseModel):
    customer_email_hash: str   # never expose raw email — hash it
    total_spent: float
    order_count: int
    first_order_at: str | None
    last_order_at: str | None


class TopCustomersResponse(BaseModel):
    currency: str
    has_data: bool
    customers: list[TopCustomer]


class AbandonmentDay(BaseModel):
    day: str             # "YYYY-MM-DD"
    cart_adds: int
    purchases: int
    abandonment_pct: float | None  # None when cart_adds == 0


class AbandonmentTrendResponse(BaseModel):
    days: int
    timezone: str
    has_data: bool
    series: list[AbandonmentDay]
    avg_abandonment_pct: float | None


class CustomerCohortAov(BaseModel):
    customers: int
    orders: int
    revenue: float
    aov: float


class FirstVsRepeatResponse(BaseModel):
    currency: str
    has_data: bool
    first: CustomerCohortAov          # customers buying for the first time in window
    repeat: CustomerCohortAov         # customers with prior order
    aov_uplift_pct: float | None       # (repeat.aov - first.aov) / first.aov


# ---------------------------------------------------------------------------
# 1. Device breakdown
# ---------------------------------------------------------------------------

@router.get("/device-breakdown", response_model=DeviceBreakdownResponse)
def get_device_breakdown(
    days: int = Query(14, ge=1, le=90),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> DeviceBreakdownResponse:
    """Visitor sessions split by device_type over the last `days` days.

    Counts DISTINCT visitor_id per device — a visitor switching devices
    counts in each, but sessions on same device dedupe. This matches
    Shopify Analytics' "Sessions by device" semantics."""
    cache_key = f"hs:dev_brk:v1:{shop}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return DeviceBreakdownResponse(**cached)

    # events.timestamp is BIGINT epoch milliseconds — convert to seconds
    # threshold to compare. (CURRENT epoch — days*86400) * 1000.
    rows = db.execute(
        text("""
            SELECT
                COALESCE(NULLIF(LOWER(device_type), ''), 'unknown') AS dev,
                COUNT(DISTINCT visitor_id) AS sessions
            FROM events
            WHERE shop_domain = :shop
              AND event_type = 'page_view'
              AND timestamp >= (EXTRACT(EPOCH FROM NOW() - (:days || ' days')::interval) * 1000)
            GROUP BY 1
            ORDER BY sessions DESC
        """),
        {"shop": shop, "days": days},
    ).mappings().all()

    total = sum(r["sessions"] for r in rows)
    slices = [
        DeviceSlice(
            device=r["dev"],
            sessions=r["sessions"],
            pct=round((r["sessions"] / total) * 100.0, 1) if total else 0.0,
        )
        for r in rows
    ]

    response = DeviceBreakdownResponse(
        days=days, total_sessions=total, has_data=total > 0, slices=slices,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 2. Top customers by LTV
# ---------------------------------------------------------------------------

@router.get("/top-customers-ltv", response_model=TopCustomersResponse)
def get_top_customers_ltv(
    limit: int = Query(10, ge=1, le=50),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> TopCustomersResponse:
    """Top customers ranked by lifetime total spend.

    PII-safe: emails are SHA-256 hashed in the response. The dashboard
    UI shows the hash truncated as "cust_a3b8f1" so the merchant can
    correlate without us echoing raw email addresses across the wire.
    """
    import hashlib

    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:topltv:v1:{shop}:{currency}:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return TopCustomersResponse(**cached)

    rows = db.execute(
        text("""
            SELECT
                customer_email,
                SUM(total_price)::float       AS total_spent,
                COUNT(*)                      AS order_count,
                MIN(created_at)               AS first_order_at,
                MAX(created_at)               AS last_order_at
            FROM shop_orders
            WHERE shop_domain = :shop
              AND currency = :currency
              AND customer_email IS NOT NULL
              AND customer_email <> ''
            GROUP BY customer_email
            ORDER BY total_spent DESC
            LIMIT :limit
        """),
        {"shop": shop, "currency": currency, "limit": limit},
    ).mappings().all()

    customers = [
        TopCustomer(
            customer_email_hash="cust_" + hashlib.sha256(r["customer_email"].encode()).hexdigest()[:8],
            total_spent=round(float(r["total_spent"] or 0), 2),
            order_count=int(r["order_count"] or 0),
            first_order_at=r["first_order_at"].isoformat() if r["first_order_at"] else None,
            last_order_at=r["last_order_at"].isoformat() if r["last_order_at"] else None,
        )
        for r in rows
    ]
    response = TopCustomersResponse(
        currency=currency, has_data=len(customers) > 0, customers=customers,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 3. Abandonment trend
# ---------------------------------------------------------------------------

@router.get("/abandonment-trend", response_model=AbandonmentTrendResponse)
def get_abandonment_trend(
    days: int = Query(14, ge=7, le=90),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> AbandonmentTrendResponse:
    """Daily cart-abandonment % over the last `days` days.

    abandonment_pct = (cart_adds - purchases) / cart_adds  (per day)
    None when cart_adds is zero — never fabricate against empty days.
    """
    tz = get_shop_timezone(db, shop) or "UTC"
    cache_key = f"hs:abndntrnd:v1:{shop}:{tz}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return AbandonmentTrendResponse(**cached)

    # events.timestamp is BIGINT epoch milliseconds. Convert to TIMESTAMPTZ
    # via to_timestamp(ms/1000), then bucket by shop's local-tz date.
    rows = db.execute(
        text("""
            WITH days AS (
                SELECT generate_series(
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date - (:days - 1),
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date,
                    INTERVAL '1 day'
                )::date AS d
            ),
            agg AS (
                SELECT
                    (to_timestamp(timestamp / 1000.0) AT TIME ZONE :tz)::date AS d,
                    COUNT(*) FILTER (WHERE event_type = 'cart_added') AS cart_adds,
                    COUNT(*) FILTER (WHERE event_type = 'purchase')   AS purchases
                FROM events
                WHERE shop_domain = :shop
                  AND timestamp >= (EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date - (:days - 1)) * 1000)
                  AND event_type IN ('cart_added', 'purchase')
                GROUP BY 1
            )
            SELECT days.d::text AS day,
                   COALESCE(agg.cart_adds, 0) AS cart_adds,
                   COALESCE(agg.purchases, 0) AS purchases
            FROM days LEFT JOIN agg USING (d)
            ORDER BY days.d
        """),
        {"shop": shop, "tz": tz, "days": days},
    ).mappings().all()

    series = []
    pct_values = []
    for r in rows:
        ca = int(r["cart_adds"] or 0)
        pu = int(r["purchases"] or 0)
        if ca > 0:
            pct = round(max(0.0, (ca - pu) / ca) * 100.0, 1)
            pct_values.append(pct)
        else:
            pct = None
        series.append(AbandonmentDay(day=r["day"], cart_adds=ca, purchases=pu, abandonment_pct=pct))

    avg_pct = round(sum(pct_values) / len(pct_values), 1) if pct_values else None
    has_data = any(s.cart_adds > 0 for s in series)
    response = AbandonmentTrendResponse(
        days=days, timezone=tz, has_data=has_data, series=series, avg_abandonment_pct=avg_pct,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 4. First-vs-repeat AOV
# ---------------------------------------------------------------------------

@router.get("/first-vs-repeat-aov", response_model=FirstVsRepeatResponse)
def get_first_vs_repeat_aov(
    days: int = Query(90, ge=14, le=365),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> FirstVsRepeatResponse:
    """AOV comparison: customers' first purchase vs repeat purchases.

    Window: last `days` days of orders. For each customer in window,
    we partition their orders by "is this their first-ever order?"
    (computed via window function over their full history)."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:fvr:v1:{shop}:{currency}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return FirstVsRepeatResponse(**cached)

    rows = db.execute(
        text("""
            WITH ranked AS (
                SELECT
                    customer_email,
                    total_price,
                    created_at,
                    ROW_NUMBER() OVER (PARTITION BY customer_email ORDER BY created_at) AS rn
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND currency = :currency
                  AND customer_email IS NOT NULL
                  AND customer_email <> ''
                  AND total_price > 0
            ),
            windowed AS (
                SELECT * FROM ranked
                WHERE created_at >= NOW() - (:days || ' days')::interval
            )
            SELECT
                COUNT(*) FILTER (WHERE rn = 1)                            AS first_orders,
                COUNT(*) FILTER (WHERE rn > 1)                            AS repeat_orders,
                COUNT(DISTINCT customer_email) FILTER (WHERE rn = 1)      AS first_customers,
                COUNT(DISTINCT customer_email) FILTER (WHERE rn > 1)      AS repeat_customers,
                COALESCE(SUM(total_price) FILTER (WHERE rn = 1), 0)::float AS first_revenue,
                COALESCE(SUM(total_price) FILTER (WHERE rn > 1), 0)::float AS repeat_revenue
            FROM windowed
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).fetchone()

    fo = int(rows[0] or 0); ro = int(rows[1] or 0)
    fc = int(rows[2] or 0); rc = int(rows[3] or 0)
    fr = float(rows[4] or 0); rr = float(rows[5] or 0)

    first = CustomerCohortAov(
        customers=fc, orders=fo, revenue=round(fr, 2),
        aov=round(fr / fo, 2) if fo > 0 else 0.0,
    )
    repeat = CustomerCohortAov(
        customers=rc, orders=ro, revenue=round(rr, 2),
        aov=round(rr / ro, 2) if ro > 0 else 0.0,
    )
    uplift = None
    if first.aov > 0:
        uplift = round(((repeat.aov - first.aov) / first.aov) * 100.0, 1)

    response = FirstVsRepeatResponse(
        currency=currency, has_data=fo + ro > 0, first=first, repeat=repeat, aov_uplift_pct=uplift,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response
