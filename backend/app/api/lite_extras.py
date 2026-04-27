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


class CountryAggregate(BaseModel):
    country_code: str         # ISO-3166-1 alpha-2 (US, IT, GB, ...)
    orders: int
    revenue: float


class OrdersByCountryResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    total_orders: int
    total_revenue: float
    countries: list[CountryAggregate]


# ── Class C response models ──

class HourBucket(BaseModel):
    hour: int        # 0..23
    orders: int
    revenue: float


class DowBucket(BaseModel):
    dow: int         # 0=Sunday … 6=Saturday (Postgres EXTRACT(DOW))
    label: str       # "Sun".."Sat"
    orders: int
    revenue: float


class OrderRhythmResponse(BaseModel):
    currency: str
    timezone: str
    days: int
    has_data: bool
    by_hour: list[HourBucket]
    by_dow: list[DowBucket]
    peak_hour: int | None
    peak_dow: int | None


class RepeatCadenceResponse(BaseModel):
    has_data: bool
    customers_with_2plus: int
    intervals_count: int           # number of (next-prev) intervals computed
    median_days: float | None
    p25_days: float | None
    p75_days: float | None
    mean_days: float | None


class TopProduct(BaseModel):
    title: str
    orders: int
    units: int
    revenue: float


class TopProductsResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    products: list[TopProduct]


# ── Class D response models ──

class DiscountCodeBucket(BaseModel):
    code: str
    orders: int
    total_discount: float
    total_revenue: float


class DiscountCodesResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    enriched_orders: int          # how many orders had discount data
    total_orders_window: int      # all orders in window (for coverage %)
    codes: list[DiscountCodeBucket]


class StatusBucket(BaseModel):
    label: str            # "paid" / "pending" / "fulfilled" / "unfulfilled" / etc.
    orders: int
    pct: float


class OrderStatusResponse(BaseModel):
    days: int
    has_data: bool
    enriched_orders: int
    financial: list[StatusBucket]
    fulfillment: list[StatusBucket]


class TaxBreakdownResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    enriched_orders: int
    total_orders_window: int
    total_revenue: float          # only enriched orders
    total_tax: float
    tax_rate_pct: float | None    # total_tax / (revenue - tax) * 100


class PaymentMethodBucket(BaseModel):
    method: str
    orders: int
    revenue: float
    pct: float


class PaymentMethodsResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    enriched_orders: int
    total_orders_window: int
    methods: list[PaymentMethodBucket]


class TopVariant(BaseModel):
    variant_id: str | None
    product_title: str
    variant_title: str | None
    sku: str | None
    units: int
    revenue: float


class TopVariantsResponse(BaseModel):
    currency: str
    days: int
    has_data: bool
    enriched_orders: int
    total_orders_window: int
    variants: list[TopVariant]


class ChurnRiskCustomer(BaseModel):
    # Hashed identifier — never raw email per PII contract
    customer_email_hash: str
    # Shopify customer_id when present, used to construct a deep-link to
    # the merchant's own Shopify admin customer detail page. NOT PII to
    # the merchant (it's their own customer in their own admin), but it
    # only ever gets sent if the merchant's own session is authenticated
    # for this shop (require_merchant_session). None for legacy / pixel-
    # only orders without Shopify customer_id populated.
    customer_id_shopify: str | None = None
    risk_score: int                     # 0-95
    risk_band: str                      # "slipping" | "at_risk" | "lapsed"
    days_since_last_order: int
    median_days_between_orders: float   # this customer's personal cadence
    overdue_factor: float               # days_since_last / median_gap
    last_order_at: str | None
    predicted_lapse_at: str | None      # last_order + 2.5×median_gap
    order_count: int
    total_spent: float                  # lifetime revenue from this customer
    suggested_action: str               # plain-English next step


class CustomerChurnForecastResponse(BaseModel):
    currency: str
    has_data: bool
    # Cold-start gate: minimum 30 customers with 2+ orders required for
    # the personal-cadence model to surface meaningful predictions.
    customers_with_2plus: int
    customers_at_risk_count: int        # customers with risk_score >= 30
    revenue_at_risk: float              # SUM of total_spent over at-risk customers
    customers: list[ChurnRiskCustomer]  # top_n ranked by (risk DESC, spend DESC)


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
                SUM(total_price)              AS total_spent,
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
                COALESCE(SUM(total_price) FILTER (WHERE rn = 1), 0) AS first_revenue,
                COALESCE(SUM(total_price) FILTER (WHERE rn > 1), 0) AS repeat_revenue
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


# ---------------------------------------------------------------------------
# 5. Orders by country (B-super F5 — extends the Live Radar map)
# ---------------------------------------------------------------------------

@router.get("/orders-by-country", response_model=OrdersByCountryResponse)
def get_orders_by_country(
    days: int = Query(30, ge=7, le=90),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> OrdersByCountryResponse:
    """Aggregate orders + revenue by country over last `days` days.

    Reads the per-shop hash `hs:order_geo:{shop_domain}` populated at
    purchase time by `app/core/geo.record_order_geo`. Field shape:
        "{CC}:{YYYY-MM-DD}:count"          -> int
        "{CC}:{YYYY-MM-DD}:revenue_{CCY}"  -> float

    No schema migration on shop_orders — geo data comes from the same
    Redis cache that powers the live-visitor map. Founder directive
    2026-04-26: "abbiamo già dati che dovrebbero entrare nel radar+map,
    è la map la nostra geo".

    Currency-aware: only sums revenue fields matching the shop's
    currency. Cross-currency edge cases (multi-store under one shop)
    aggregate the count but skip foreign-currency revenue."""
    from datetime import datetime, timezone, timedelta

    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:obc:v1:{shop}:{currency}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return OrdersByCountryResponse(**cached)

    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("lite_extras.orders_by_country.no_redis")
            return OrdersByCountryResponse(
                currency=currency, days=days, has_data=False,
                total_orders=0, total_revenue=0.0, countries=[],
            )
        key = f"hs:order_geo:{shop}"
        raw = rc.hgetall(key) or {}
    except Exception as exc:
        log.warning("orders-by-country: redis read failed: %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("lite_extras.orders_by_country")
        return OrdersByCountryResponse(
            currency=currency, days=days, has_data=False,
            total_orders=0, total_revenue=0.0, countries=[],
        )

    today = datetime.now(timezone.utc).date()
    valid_dates = {(today - timedelta(days=i)).isoformat() for i in range(days)}

    by_cc: dict[str, dict[str, float]] = {}
    for raw_field, raw_value in raw.items():
        field = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        parts = field.split(":")
        if len(parts) < 3:
            continue
        cc, date, metric = parts[0], parts[1], parts[2]
        if date not in valid_dates:
            continue
        bucket = by_cc.setdefault(cc, {"count": 0, "revenue": 0.0})
        if metric == "count":
            try: bucket["count"] += int(value)
            except (TypeError, ValueError): pass
        elif metric.startswith("revenue_"):
            metric_ccy = metric.split("_", 1)[1]
            if metric_ccy == currency:
                try: bucket["revenue"] += float(value)
                except (TypeError, ValueError): pass

    countries = [
        CountryAggregate(
            country_code=cc, orders=int(b["count"]), revenue=round(b["revenue"], 2),
        )
        for cc, b in by_cc.items() if b["count"] > 0
    ]
    countries.sort(key=lambda c: (-c.revenue, -c.orders))

    total_orders = sum(c.orders for c in countries)
    total_revenue = round(sum(c.revenue for c in countries), 2)

    response = OrdersByCountryResponse(
        currency=currency, days=days,
        has_data=total_orders > 0,
        total_orders=total_orders,
        total_revenue=total_revenue,
        countries=countries,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 6. Order rhythm — hour-of-day + day-of-week patterns (Class C1)
# ---------------------------------------------------------------------------

_DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


@router.get("/order-rhythm", response_model=OrderRhythmResponse)
def get_order_rhythm(
    days: int = Query(30, ge=7, le=365),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> OrderRhythmResponse:
    """Order rhythm — when (hour-of-day + day-of-week) the merchant's
    customers buy. Both buckets in shop's local timezone so "Tuesday
    9am" means 9am for the merchant, not UTC."""
    currency = get_shop_currency(db, shop) or "USD"
    tz = get_shop_timezone(db, shop) or "UTC"
    cache_key = f"hs:rhythm:v1:{shop}:{currency}:{tz}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return OrderRhythmResponse(**cached)

    rows = db.execute(
        text("""
            SELECT
                EXTRACT(HOUR FROM (created_at AT TIME ZONE :tz))::int  AS hour,
                EXTRACT(DOW  FROM (created_at AT TIME ZONE :tz))::int  AS dow,
                COUNT(*)                                                AS orders,
                COALESCE(SUM(total_price), 0)                           AS revenue
            FROM shop_orders
            WHERE shop_domain = :shop
              AND currency = :currency
              AND total_price > 0
              AND created_at >= NOW() - (:days || ' days')::interval
            GROUP BY 1, 2
        """),
        {"shop": shop, "currency": currency, "tz": tz, "days": days},
    ).mappings().all()

    hour_acc = {h: {"orders": 0, "revenue": 0.0} for h in range(24)}
    dow_acc  = {d: {"orders": 0, "revenue": 0.0} for d in range(7)}
    for r in rows:
        h = int(r["hour"]); d = int(r["dow"])
        n = int(r["orders"]); rev = float(r["revenue"])
        hour_acc[h]["orders"] += n; hour_acc[h]["revenue"] += rev
        dow_acc[d]["orders"]  += n; dow_acc[d]["revenue"]  += rev

    by_hour = [
        HourBucket(hour=h, orders=hour_acc[h]["orders"], revenue=round(hour_acc[h]["revenue"], 2))
        for h in range(24)
    ]
    by_dow = [
        DowBucket(dow=d, label=_DOW_LABELS[d],
                  orders=dow_acc[d]["orders"], revenue=round(dow_acc[d]["revenue"], 2))
        for d in range(7)
    ]
    total = sum(b.orders for b in by_hour)
    peak_hour = max(by_hour, key=lambda b: b.orders).hour if total > 0 else None
    peak_dow  = max(by_dow,  key=lambda b: b.orders).dow  if total > 0 else None

    response = OrderRhythmResponse(
        currency=currency, timezone=tz, days=days, has_data=total > 0,
        by_hour=by_hour, by_dow=by_dow,
        peak_hour=peak_hour, peak_dow=peak_dow,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 7. Repeat cadence — time between consecutive orders per customer (Class C2)
# ---------------------------------------------------------------------------

@router.get("/repeat-cadence", response_model=RepeatCadenceResponse)
def get_repeat_cadence(
    days: int = Query(180, ge=30, le=730),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> RepeatCadenceResponse:
    """For each customer with 2+ orders in the last `days` days,
    compute days between consecutive orders. Return percentile stats."""
    cache_key = f"hs:repcad:v1:{shop}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return RepeatCadenceResponse(**cached)

    rows = db.execute(
        text("""
            WITH ranked AS (
                SELECT
                    customer_email,
                    created_at,
                    LAG(created_at) OVER (
                        PARTITION BY customer_email ORDER BY created_at
                    ) AS prev_at
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND customer_email IS NOT NULL
                  AND customer_email <> ''
                  AND created_at >= NOW() - (:days || ' days')::interval
            )
            SELECT
                EXTRACT(EPOCH FROM (created_at - prev_at)) / 86400.0 AS gap_days
            FROM ranked
            WHERE prev_at IS NOT NULL
        """),
        {"shop": shop, "days": days},
    ).fetchall()

    gaps = sorted(float(r[0]) for r in rows if r[0] is not None and float(r[0]) >= 0)
    if not gaps:
        response = RepeatCadenceResponse(
            has_data=False, customers_with_2plus=0, intervals_count=0,
            median_days=None, p25_days=None, p75_days=None, mean_days=None,
        )
        cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
        return response

    def _pct(p: float) -> float:
        idx = max(0, min(len(gaps) - 1, int(round(p * (len(gaps) - 1)))))
        return round(gaps[idx], 1)

    customer_count = db.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT customer_email
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND customer_email IS NOT NULL
                  AND customer_email <> ''
                  AND created_at >= NOW() - (:days || ' days')::interval
                GROUP BY customer_email
                HAVING COUNT(*) >= 2
            ) t
        """),
        {"shop": shop, "days": days},
    ).scalar() or 0

    response = RepeatCadenceResponse(
        has_data=True,
        customers_with_2plus=int(customer_count),
        intervals_count=len(gaps),
        median_days=_pct(0.50),
        p25_days=_pct(0.25),
        p75_days=_pct(0.75),
        mean_days=round(sum(gaps) / len(gaps), 1),
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 8. Top products — most-bought items by revenue (Class C3)
# ---------------------------------------------------------------------------
#
# Original audit asked for "Variants performance (size/color)". Empirical
# check on shop_orders.line_items JSONB shows the schema today doesn't
# carry variant_id — items have {price, title, handle, quantity,
# product_url}. Variant-level requires either webhook expansion OR a
# pixel-side payload change. Both are TIER_2-adjacent.
#
# Pragmatic delivery: ship "top products over last N days" instead.
# Same competitive pitch (Shopify Free / Better Reports show this),
# zero schema risk. Variants stay R-blocker:tier_2-approval.

@router.get("/top-products", response_model=TopProductsResponse)
def get_top_products(
    days: int = Query(30, ge=7, le=180),
    limit: int = Query(10, ge=1, le=50),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> TopProductsResponse:
    """Top products by revenue over last `days` days. Joins each
    line_items JSONB element to its parent order so we sum across
    every line in every matching order. NULL/blank titles roll up
    into 'Untitled product' rather than dropping them."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:topprod:v1:{shop}:{currency}:{days}:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return TopProductsResponse(**cached)

    rows = db.execute(
        text("""
            SELECT
                COALESCE(NULLIF(li->>'title', ''), 'Untitled product') AS title,
                COUNT(DISTINCT so.id)                                   AS orders,
                COALESCE(SUM((li->>'quantity')::int), 0)                AS units,
                COALESCE(SUM(
                    (li->>'quantity')::numeric * (li->>'price')::numeric
                ), 0)                                                   AS revenue
            FROM shop_orders so,
                 LATERAL jsonb_array_elements(so.line_items) li
            WHERE so.shop_domain = :shop
              AND so.currency = :currency
              AND so.created_at >= NOW() - (:days || ' days')::interval
              AND li->>'title' IS NOT NULL
            GROUP BY 1
            ORDER BY revenue DESC, orders DESC
            LIMIT :limit
        """),
        {"shop": shop, "currency": currency, "days": days, "limit": limit},
    ).mappings().all()

    products = [
        TopProduct(
            title=r["title"], orders=int(r["orders"] or 0),
            units=int(r["units"] or 0), revenue=round(float(r["revenue"] or 0), 2),
        )
        for r in rows
    ]
    response = TopProductsResponse(
        currency=currency, days=days,
        has_data=len(products) > 0, products=products,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# Class D — schema-enriched analytics (populated by spark-pixel.js v14+)
# ---------------------------------------------------------------------------
#
# Each endpoint follows the same shape: report `enriched_orders` +
# `total_orders_window` so the dashboard can show coverage ("47/120
# orders carry discount data — older orders pre-pixel-v14 stay
# uncounted"). has_data=true only when at least 1 enriched order
# exists in the window.

@router.get("/discount-codes", response_model=DiscountCodesResponse)
def get_discount_codes(
    days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> DiscountCodesResponse:
    """Top discount codes by usage in last N days. Computes total
    discount + total revenue per code so the merchant sees ROI per code."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:disc:v1:{shop}:{currency}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return DiscountCodesResponse(**cached)

    total_window = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    rows = db.execute(
        text("""
            SELECT
                code,
                COUNT(*) AS orders,
                COALESCE(SUM(discount_amount), 0) AS total_discount,
                COALESCE(SUM(total_price),     0) AS total_revenue
            FROM shop_orders so,
                 LATERAL jsonb_array_elements_text(so.discount_codes) AS code
            WHERE so.shop_domain = :shop
              AND so.currency = :currency
              AND so.created_at >= NOW() - (:days || ' days')::interval
              AND so.discount_codes IS NOT NULL
              AND jsonb_array_length(so.discount_codes) > 0
            GROUP BY code
            ORDER BY orders DESC
            LIMIT 20
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).mappings().all()

    enriched = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
              AND discount_codes IS NOT NULL
              AND jsonb_array_length(discount_codes) > 0
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    codes = [
        DiscountCodeBucket(
            code=str(r["code"])[:64],
            orders=int(r["orders"] or 0),
            total_discount=round(float(r["total_discount"] or 0), 2),
            total_revenue=round(float(r["total_revenue"] or 0), 2),
        )
        for r in rows
    ]
    response = DiscountCodesResponse(
        currency=currency, days=days,
        has_data=enriched > 0,
        enriched_orders=int(enriched),
        total_orders_window=int(total_window),
        codes=codes,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


@router.get("/order-status", response_model=OrderStatusResponse)
def get_order_status(
    days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> OrderStatusResponse:
    """Financial + fulfillment status breakdown for last N days.

    NB: pixel-time defaults are 'paid' + 'unfulfilled' — without
    Protected-Customer-Data webhook approval, status post-purchase
    transitions (refunds, fulfillment) aren't reflected. The Lite
    tile copy makes this explicit so the merchant doesn't read it
    as full lifecycle truth."""
    cache_key = f"hs:status:v1:{shop}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return OrderStatusResponse(**cached)

    fin_rows = db.execute(
        text("""
            SELECT COALESCE(financial_status, 'unknown') AS label, COUNT(*) AS orders
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at >= NOW() - (:days || ' days')::interval
              AND financial_status IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC
        """),
        {"shop": shop, "days": days},
    ).mappings().all()

    ful_rows = db.execute(
        text("""
            SELECT COALESCE(fulfillment_status, 'unknown') AS label, COUNT(*) AS orders
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at >= NOW() - (:days || ' days')::interval
              AND fulfillment_status IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC
        """),
        {"shop": shop, "days": days},
    ).mappings().all()

    enriched = sum(int(r["orders"] or 0) for r in fin_rows) or sum(int(r["orders"] or 0) for r in ful_rows)

    def _to_buckets(rows):
        total = sum(int(r["orders"] or 0) for r in rows)
        return [
            StatusBucket(
                label=str(r["label"]),
                orders=int(r["orders"] or 0),
                pct=round((int(r["orders"] or 0) / total) * 100.0, 1) if total else 0.0,
            )
            for r in rows
        ]

    response = OrderStatusResponse(
        days=days, has_data=enriched > 0,
        enriched_orders=enriched,
        financial=_to_buckets(fin_rows),
        fulfillment=_to_buckets(ful_rows),
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


@router.get("/tax-breakdown", response_model=TaxBreakdownResponse)
def get_tax_breakdown(
    days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> TaxBreakdownResponse:
    """Total tax + effective rate over enriched orders in window."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:tax:v1:{shop}:{currency}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return TaxBreakdownResponse(**cached)

    total_window = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    row = db.execute(
        text("""
            SELECT
                COUNT(*)                                     AS enriched,
                COALESCE(SUM(total_price), 0)                AS revenue,
                COALESCE(SUM(tax_amount),  0)                AS tax
            FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
              AND tax_amount IS NOT NULL
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).fetchone()

    enriched = int(row[0] or 0)
    revenue  = float(row[1] or 0)
    tax      = float(row[2] or 0)
    tax_rate = None
    pre_tax_rev = revenue - tax
    if pre_tax_rev > 0 and tax > 0:
        tax_rate = round((tax / pre_tax_rev) * 100.0, 2)

    response = TaxBreakdownResponse(
        currency=currency, days=days,
        has_data=enriched > 0,
        enriched_orders=enriched,
        total_orders_window=int(total_window),
        total_revenue=round(revenue, 2),
        total_tax=round(tax, 2),
        tax_rate_pct=tax_rate,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


@router.get("/payment-methods", response_model=PaymentMethodsResponse)
def get_payment_methods(
    days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> PaymentMethodsResponse:
    """Order count + revenue split by payment_method (gateway)."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:pmnt:v1:{shop}:{currency}:{days}"
    cached = cache_get(cache_key)
    if cached:
        return PaymentMethodsResponse(**cached)

    total_window = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    rows = db.execute(
        text("""
            SELECT
                COALESCE(payment_method, 'unknown') AS method,
                COUNT(*)                            AS orders,
                COALESCE(SUM(total_price), 0)       AS revenue
            FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
              AND payment_method IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).mappings().all()

    enriched = sum(int(r["orders"] or 0) for r in rows)
    methods = [
        PaymentMethodBucket(
            method=str(r["method"])[:64],
            orders=int(r["orders"] or 0),
            revenue=round(float(r["revenue"] or 0), 2),
            pct=round((int(r["orders"] or 0) / enriched) * 100.0, 1) if enriched else 0.0,
        )
        for r in rows
    ]

    response = PaymentMethodsResponse(
        currency=currency, days=days,
        has_data=enriched > 0,
        enriched_orders=enriched,
        total_orders_window=int(total_window),
        methods=methods,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# Top variants — closes the original Class D "Variants performance" gap
# ---------------------------------------------------------------------------
#
# Pixel v15 (2026-04-26) sends line_items with variant_id/variant_title/
# sku/quantity/price extracted from Shopify checkout context. This
# endpoint groups by variant_id and ranks by revenue.
#
# Variants without an explicit variant_id (older pixel + cases where
# Shopify didn't expose it) collapse into "(no variant)" bucket so
# 100% of revenue surfaces somewhere.

@router.get("/top-variants", response_model=TopVariantsResponse)
def get_top_variants(
    days: int = Query(30, ge=7, le=180),
    limit: int = Query(10, ge=1, le=50),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> TopVariantsResponse:
    """Top-selling variants over last `days` days. Joins each
    line_items[] element to its parent order via LATERAL, groups
    by (product_title, variant_title) so different colors of the
    same product surface separately."""
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:topvar:v1:{shop}:{currency}:{days}:{limit}"
    cached = cache_get(cache_key)
    if cached:
        return TopVariantsResponse(**cached)

    total_window = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    enriched = db.execute(
        text("""
            SELECT COUNT(*) FROM shop_orders
            WHERE shop_domain = :shop AND currency = :currency
              AND created_at >= NOW() - (:days || ' days')::interval
              AND line_items IS NOT NULL
              AND jsonb_array_length(line_items) > 0
              AND EXISTS (
                  SELECT 1 FROM jsonb_array_elements(line_items) li
                  WHERE li ? 'variant_id'
              )
        """),
        {"shop": shop, "currency": currency, "days": days},
    ).scalar() or 0

    rows = db.execute(
        text("""
            SELECT
                li->>'variant_id'    AS variant_id,
                COALESCE(NULLIF(li->>'product_title', ''), 'Untitled product') AS product_title,
                NULLIF(li->>'variant_title', '')  AS variant_title,
                NULLIF(li->>'sku', '')             AS sku,
                COALESCE(SUM((li->>'quantity')::int), 0) AS units,
                COALESCE(SUM(
                    (li->>'quantity')::numeric * (li->>'price')::numeric
                ), 0) AS revenue
            FROM shop_orders so,
                 LATERAL jsonb_array_elements(so.line_items) li
            WHERE so.shop_domain = :shop
              AND so.currency = :currency
              AND so.created_at >= NOW() - (:days || ' days')::interval
              AND li ? 'variant_id'
            GROUP BY 1, 2, 3, 4
            ORDER BY revenue DESC, units DESC
            LIMIT :limit
        """),
        {"shop": shop, "currency": currency, "days": days, "limit": limit},
    ).mappings().all()

    variants = [
        TopVariant(
            variant_id=r["variant_id"],
            product_title=str(r["product_title"]),
            variant_title=r["variant_title"],
            sku=r["sku"],
            units=int(r["units"] or 0),
            revenue=round(float(r["revenue"] or 0), 2),
        )
        for r in rows
    ]
    response = TopVariantsResponse(
        currency=currency, days=days,
        has_data=len(variants) > 0,
        enriched_orders=int(enriched),
        total_orders_window=int(total_window),
        variants=variants,
    )
    cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
    return response


# ---------------------------------------------------------------------------
# 12. Customer-level Churn Forecast — 5th open-lane competitor moat
# ---------------------------------------------------------------------------
# Born 2026-04-27 from the brutal Lite vs $0-70 audit. None of the 12
# competitors ship per-customer churn risk in the price band: Lifetimely
# does cohort-level retention, Datadrew does RFM tags at $99, BeProfit
# only at $149. We close this lane at the entry tier with a deterministic,
# explainable, per-customer model.
#
# Why deterministic (not LLM):
#   * 10k merchants × 1k customers each = 10M rows. LLM is intractable.
#   * Score must be reproducible — merchant audit-grade, not "ai magic".
#   * Per CLAUDE.md §2 rule 9: deterministic first, LLM only when
#     indispensable. This is NOT indispensable.
#
# Model:
#   For each customer with ≥2 orders in the last 730 days, compute their
#   personal cadence (median days between consecutive orders). Then:
#       overdue_factor = days_since_last_order / personal_median_gap
#       factor < 1.0  → not at risk (still within typical window)
#       factor 1.0-1.5 → "slipping"  (score 30-50)
#       factor 1.5-2.5 → "at_risk"   (score 50-80)
#       factor >= 2.5  → "lapsed"    (score 80-95, capped — no certainty)
#   Predicted lapse date = last_order + (median_gap × 2.5).
#
# Cold-start: requires ≥ 30 customers with 2+ orders. Below threshold,
# returns has_data=false with the cohort count so the UI can explain
# the wait clearly.
#
# Ranking: top-N by (risk_score DESC, total_spent DESC) — the most
# valuable at-risk customers come first (where saving has the highest
# revenue lift).
# ---------------------------------------------------------------------------

# Cold-start threshold — below this many customers with 2+ orders the
# personal-cadence model has too few signals to be trustworthy. Set
# conservatively at 30 (3× the typical Top-N display) so the threshold
# itself isn't a red herring.
_CHURN_MIN_CUSTOMERS = 30


def _churn_score_and_band(
    days_since_last: float | None, median_gap: float | None
) -> tuple[int, str, float]:
    """Deterministic scoring. Returns (risk_score 0-95, band label, overdue_factor).

    Band labels match the loss-prevention narrative (CLAUDE.md §5):
    - "not_at_risk": still within personal cadence (skipped from response)
    - "slipping":    overdue_factor 1.0–1.5 → score 30–50
    - "at_risk":     overdue_factor 1.5–2.5 → score 50–80
    - "lapsed":      overdue_factor >= 2.5  → score 80–95 (cap = no false certainty)
    """
    if not days_since_last or not median_gap or median_gap <= 0:
        return 0, "not_at_risk", 0.0
    factor = float(days_since_last) / float(median_gap)
    if factor < 1.0:
        return 0, "not_at_risk", factor
    if factor < 1.5:
        return int(round(30 + (factor - 1.0) * 40)), "slipping", factor
    if factor < 2.5:
        return int(round(50 + (factor - 1.5) * 30)), "at_risk", factor
    # Cap at 95 — we never claim certainty
    return min(int(round(80 + (factor - 2.5) * 5)), 95), "lapsed", factor


def _churn_action(band: str) -> str:
    """Plain-English next step. Idiot-proof copy per CLAUDE.md §5 filter 2."""
    if band == "slipping":
        return "Light touch: send a personal note before the gap widens."
    if band == "at_risk":
        return "Win-back sequence: 'we miss you' email with a soft incentive."
    if band == "lapsed":
        return "Last-chance offer: time-bound discount on their favorite category."
    return "Monitor."


@router.get(
    "/customer-churn-forecast",
    response_model=CustomerChurnForecastResponse,
)
def get_customer_churn_forecast(
    top_n: int = Query(10, ge=1, le=50),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
) -> CustomerChurnForecastResponse:
    """Per-customer churn risk based on personal-cadence overdue factor.

    PII contract: emails are SHA-256 hashed in the response (cust_<8hex>),
    matching the existing `top-customers-ltv` pattern. No raw email
    crosses the wire."""
    import hashlib
    import time

    # Currency is reported in the response (for top_spent display + UI
    # labels) but does NOT filter the query: a customer who buys in USD
    # AND in EUR is still ONE customer for churn purposes — we want to
    # see if they're slipping regardless of which currency they used.
    # Pre-fix the query had `currency = :currency` which excluded any
    # cross-currency customer entirely (data loss, not just display).
    currency = get_shop_currency(db, shop) or "USD"
    cache_key = f"hs:churn:v1:{shop}:{top_n}"
    cached = cache_get(cache_key)
    if cached:
        return CustomerChurnForecastResponse(**cached)

    # Cache stampede protection: this CTE is heavy (PERCENTILE_CONT +
    # LAG window over 730d of orders). Without serialization, N
    # concurrent requests on a cold cache trigger N parallel queries
    # for the same merchant — at scale, a single dashboard refresh by
    # multiple users could pin a Postgres connection per user. SETNX
    # lock with 30s TTL: only one worker computes; the rest wait up
    # to 10s for the cache fill before falling through to compute on
    # their own (lock holder slow / dead).
    from app.core.redis_client import _client as _redis_client
    lock_key = f"hs:churn:lock:v1:{shop}:{top_n}"
    rc = _redis_client()
    if rc is not None:
        try:
            lock_acquired = rc.set(lock_key, "1", nx=True, ex=30)
        except Exception:
            lock_acquired = True  # fail-open: better to compute than block
        if not lock_acquired:
            # Another worker is filling — poll cache for up to 10s
            for _ in range(20):  # 20 × 0.5s = 10s budget
                time.sleep(0.5)
                cached2 = cache_get(cache_key)
                if cached2:
                    return CustomerChurnForecastResponse(**cached2)
            # Lock holder slow/dead — fall through and compute ourselves

    # Statement timeout: this CTE walks every order in the last 730d
    # for the shop. At 10k merchants × 100k orders/shop the worst-case
    # is ~5s; we bound it explicitly so a slow query never blocks an
    # entire uvicorn worker.
    db.execute(text("SET LOCAL statement_timeout = '5s'"))

    # Single SQL pass: customer aggregates + median gap via percentile_cont
    # + days_since_last via NOW() arithmetic. Postgres-native, no Python
    # loop over per-customer queries.
    #
    # Filters applied:
    # - financial_status NOT IN ('refunded', 'voided'): a fully-refunded
    #   order is NOT a sale; a voided order never completed. Counting
    #   either as "they bought" corrupts the churn signal — a customer
    #   who refunded everything looks identical to one who's about to
    #   buy again, which they're not. partially_refunded customers ARE
    #   counted: they kept SOMETHING, the relationship is alive.
    # - currency filter REMOVED (was: AND currency = :currency). Multi-
    #   currency shops legitimately have customers buying in different
    #   currencies; the churn signal is order frequency, not amount, so
    #   currency mismatch is irrelevant to "are they still buying?"
    # Identity key: prefer customer_id (Shopify's stable cross-email ID
    # populated by orders/create webhook) over customer_email. A customer
    # who changes email between orders OR has typos gets correctly
    # collapsed into ONE identity. Falls back to email when customer_id
    # is null (legacy / pixel-only orders that lack the customer_id).
    # The SHA-256 hash for PII output runs on the identity value, so
    # the hash is stable per Shopify customer regardless of email churn.
    rows = db.execute(
        text("""
            WITH customer_orders AS (
                SELECT
                    COALESCE(NULLIF(customer_id, ''), customer_email) AS identity,
                    customer_email,
                    customer_id,
                    created_at,
                    total_price,
                    LAG(created_at) OVER (
                        PARTITION BY COALESCE(NULLIF(customer_id, ''), customer_email)
                        ORDER BY created_at
                    ) AS prev_at
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND customer_email IS NOT NULL
                  AND customer_email <> ''
                  AND created_at >= NOW() - INTERVAL '730 days'
                  AND (
                      financial_status IS NULL
                      OR financial_status NOT IN ('refunded', 'voided')
                  )
            ),
            customer_stats AS (
                SELECT
                    identity,
                    -- Surface the most-recent email under this identity for
                    -- display (in case the same customer used multiple
                    -- emails — the latest one is what they recognize).
                    (ARRAY_AGG(customer_email ORDER BY created_at DESC))[1] AS display_email,
                    -- Shopify customer_id (when populated) for deep-linking
                    -- to the merchant's Shopify admin customer page. Picks
                    -- the most-recent non-null value associated with this
                    -- identity. Null when all orders are pixel-only.
                    (ARRAY_AGG(customer_id ORDER BY created_at DESC) FILTER (WHERE customer_id IS NOT NULL AND customer_id <> ''))[1] AS shopify_customer_id,
                    COUNT(*)               AS order_count,
                    SUM(total_price)       AS total_spent,
                    MAX(created_at)        AS last_order_at
                FROM customer_orders
                GROUP BY identity
                HAVING COUNT(*) >= 2
            ),
            customer_gaps AS (
                SELECT
                    identity,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (created_at - prev_at)) / 86400.0
                    ) AS median_gap_days
                FROM customer_orders
                WHERE prev_at IS NOT NULL
                GROUP BY identity
            )
            SELECT
                cs.identity,
                cs.display_email,
                cs.shopify_customer_id,
                cs.order_count,
                cs.total_spent,
                cs.last_order_at,
                EXTRACT(EPOCH FROM (NOW() - cs.last_order_at)) / 86400.0 AS days_since_last,
                cg.median_gap_days
            FROM customer_stats cs
            JOIN customer_gaps cg USING (identity)
        """),
        {"shop": shop},
    ).mappings().all()

    customers_with_2plus = len(rows)

    # Cold-start gate: not enough cohort to surface meaningful predictions
    if customers_with_2plus < _CHURN_MIN_CUSTOMERS:
        response = CustomerChurnForecastResponse(
            currency=currency,
            has_data=False,
            customers_with_2plus=customers_with_2plus,
            customers_at_risk_count=0,
            revenue_at_risk=0.0,
            customers=[],
        )
        cache_set(cache_key, response.model_dump(), CACHE_TTL_S)
        return response

    # Score every customer; collect at-risk only
    at_risk: list[ChurnRiskCustomer] = []
    revenue_at_risk = 0.0
    for r in rows:
        score, band, factor = _churn_score_and_band(
            float(r["days_since_last"] or 0),
            float(r["median_gap_days"] or 0),
        )
        if score < 30:
            continue  # not_at_risk — skip
        last = r["last_order_at"]
        median_gap = float(r["median_gap_days"] or 0)
        predicted_lapse = None
        if last and median_gap > 0:
            from datetime import timedelta
            predicted_lapse_dt = last + timedelta(days=median_gap * 2.5)
            predicted_lapse = predicted_lapse_dt.isoformat()
        spent = round(float(r["total_spent"] or 0), 2)
        revenue_at_risk += spent
        # Hash the IDENTITY (customer_id when present, else email) so the
        # hash is stable across email changes for the same Shopify customer.
        # The customer_email_hash response field name is preserved for
        # backward compatibility with the existing frontend consumer.
        identity_value = str(r["identity"] or r["display_email"] or "")
        shopify_cid = r.get("shopify_customer_id")
        at_risk.append(ChurnRiskCustomer(
            customer_email_hash=(
                "cust_" + hashlib.sha256(identity_value.encode()).hexdigest()[:8]
            ),
            customer_id_shopify=str(shopify_cid) if shopify_cid else None,
            risk_score=score,
            risk_band=band,
            days_since_last_order=int(r["days_since_last"] or 0),
            median_days_between_orders=round(median_gap, 1),
            overdue_factor=round(factor, 2),
            last_order_at=last.isoformat() if last else None,
            predicted_lapse_at=predicted_lapse,
            order_count=int(r["order_count"] or 0),
            total_spent=spent,
            suggested_action=_churn_action(band),
        ))

    # Rank by (risk_score DESC, total_spent DESC) — most valuable at-risk first
    at_risk.sort(key=lambda c: (-c.risk_score, -c.total_spent))
    top = at_risk[:top_n]

    response = CustomerChurnForecastResponse(
        currency=currency,
        has_data=len(at_risk) > 0,
        customers_with_2plus=customers_with_2plus,
        customers_at_risk_count=len(at_risk),
        revenue_at_risk=round(revenue_at_risk, 2),
        customers=top,
    )
    cache_set(cache_key, response.model_dump(), 300)  # 5min cache (heavier query)
    # Release the SETNX compute lock — other waiters return from cache
    # immediately on their next poll instead of burning the full 10s budget.
    if rc is not None:
        try:
            rc.delete(lock_key)
        except Exception as exc:  # SILENT-EXCEPT-OK: lock TTLs in 30s, non-critical
            log.debug("churn lock release failed: %s", exc)
    return response
