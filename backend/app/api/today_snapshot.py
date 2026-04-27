"""
GET /analytics/today-snapshot — Day-1 base analytics for the Lite floor.

Closes the gap exposed in the 2026-04-25 audit: every cheap Shopify
analytics tool (free Shopify, Lifetimely Free, OrderMetrics, Better
Reports) shows revenue today, orders, AOV, sessions, conversion rate,
and new-vs-returning split as the FIRST thing a merchant sees. Lite
historically jumped straight into RARS / peers / P&L / cassettoni —
the intelligence layer — without grounding the merchant in the basic
"where you stand right now" pulse. A merchant on day-1 reading €39/mo
expects to see today's numbers before any leak detection.

Six KPIs in one endpoint, all sourced from real DB rows:
  - revenue        — SUM(shop_orders.total_price) WHERE today
  - orders         — COUNT(*)
  - aov            — AVG(total_price > 0)
  - sessions       — COUNT(DISTINCT visitor_id) FROM events page_view
  - conversion_rate — orders / sessions  (None when sessions == 0)
  - new_vs_returning — first-order email vs repeat in the day's window

Each KPI carries today + yesterday + delta_pct (None when yesterday
is zero — never fabricate "+∞%"). Top 5 sellers by today's revenue
ride alongside as the merchant's "what sold today" read.

Currency-aware via revenue_metrics.get_shop_currency. Timezone-aware
so "today" is the merchant's calendar day, not UTC. Cached 60s in
Redis keyed by (shop, today's date) — the cache key carries the date
so it auto-rotates at midnight and survives worker restarts.

Auth: require_merchant_session — Lite-accessible.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.core.redis_client import cache_get, cache_set
from app.services.revenue_metrics import get_shop_currency, get_shop_timezone

log = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class DayMetrics(BaseModel):
    revenue: float
    orders: int
    aov: float
    sessions: int
    conversion_rate_pct: float | None
    new_customers: int
    returning_customers: int


class Deltas(BaseModel):
    revenue_pct: float | None
    orders_pct: float | None
    aov_pct: float | None
    sessions_pct: float | None
    conversion_rate_pct_delta: float | None


class TopSeller(BaseModel):
    product_title: str
    revenue: float
    units_sold: int


class TodaySnapshotResponse(BaseModel):
    currency: str
    timezone: str
    today_iso: str
    has_data: bool
    today: DayMetrics
    yesterday: DayMetrics
    deltas: Deltas
    top_sellers_today: list[TopSeller]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _delta_pct(now: float, prev: float) -> float | None:
    """Percent change from `prev` to `now`. None when prev is zero so
    we never fabricate "+∞%" or "+100%" against a zero baseline."""
    if prev <= 0:
        return None
    return round(((now - prev) / prev) * 100.0, 1)


def _conversion_pct(orders: int, sessions: int) -> float | None:
    if sessions <= 0:
        return None
    return round((orders / sessions) * 100.0, 2)


def _query_orders(db: Session, shop: str, currency: str, tz: str) -> dict:
    """Today + yesterday revenue / orders / AOV in shop's currency + tz."""
    row = db.execute(
        text("""
            WITH p AS (
                SELECT
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date     AS today_d,
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date - 1 AS yesterday_d
            )
            SELECT
                COALESCE(SUM(CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.today_d
                                  THEN so.total_price ELSE 0 END), 0)         AS rev_today,
                COUNT(*) FILTER (WHERE (so.created_at AT TIME ZONE :tz)::date = p.today_d)
                                                                              AS orders_today,
                COALESCE(AVG(so.total_price) FILTER (WHERE (so.created_at AT TIME ZONE :tz)::date = p.today_d
                                                       AND so.total_price > 0), 0)
                                                                              AS aov_today,
                COALESCE(SUM(CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.yesterday_d
                                  THEN so.total_price ELSE 0 END), 0)         AS rev_yesterday,
                COUNT(*) FILTER (WHERE (so.created_at AT TIME ZONE :tz)::date = p.yesterday_d)
                                                                              AS orders_yesterday,
                COALESCE(AVG(so.total_price) FILTER (WHERE (so.created_at AT TIME ZONE :tz)::date = p.yesterday_d
                                                       AND so.total_price > 0), 0)
                                                                              AS aov_yesterday
            FROM shop_orders so, p
            WHERE so.shop_domain = :shop AND so.currency = :currency
        """),
        {"shop": shop, "currency": currency, "tz": tz},
    ).fetchone()
    if row is None:
        return {k: 0 for k in ("rev_today", "orders_today", "aov_today",
                               "rev_yesterday", "orders_yesterday", "aov_yesterday")}
    return {
        "rev_today": round(float(row[0] or 0), 2),
        "orders_today": int(row[1] or 0),
        "aov_today": round(float(row[2] or 0), 2),
        "rev_yesterday": round(float(row[3] or 0), 2),
        "orders_yesterday": int(row[4] or 0),
        "aov_yesterday": round(float(row[5] or 0), 2),
    }


def _query_sessions(db: Session, shop: str, tz: str) -> dict:
    """Today + yesterday distinct visitors. events.timestamp is bigint
    epoch-ms — convert via EXTRACT(EPOCH FROM <date>)::bigint*1000."""
    row = db.execute(
        text("""
            WITH p AS (
                SELECT
                    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date)::bigint * 1000        AS today_ms,
                    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date - 1)::bigint * 1000    AS yesterday_ms,
                    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date + 1)::bigint * 1000    AS tomorrow_ms
            )
            SELECT
                COUNT(DISTINCT CASE WHEN e.timestamp >= p.today_ms
                                    THEN e.visitor_id END)                          AS sess_today,
                COUNT(DISTINCT CASE WHEN e.timestamp >= p.yesterday_ms
                                     AND e.timestamp <  p.today_ms
                                    THEN e.visitor_id END)                          AS sess_yesterday
            FROM events e, p
            WHERE e.shop_domain = :shop
              AND e.event_type = 'page_view'
              AND e.timestamp >= p.yesterday_ms
              AND e.timestamp <  p.tomorrow_ms
        """),
        {"shop": shop, "tz": tz},
    ).fetchone()
    if row is None:
        return {"sess_today": 0, "sess_yesterday": 0}
    return {"sess_today": int(row[0] or 0), "sess_yesterday": int(row[1] or 0)}


def _query_new_returning(db: Session, shop: str, currency: str, tz: str) -> dict:
    """For today + yesterday separately: how many of the day's customer
    emails are placing their FIRST order ever (= new) vs returning. The
    boundary is "first ever order across all-time history", not "first
    in 30 days" — matches Shopify-native terminology."""
    row = db.execute(
        text("""
            WITH p AS (
                SELECT
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date     AS today_d,
                    (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date - 1 AS yesterday_d
            ),
            firsts AS (
                SELECT customer_email, MIN(created_at) AS first_at
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND customer_email IS NOT NULL
                  AND currency = :currency
                GROUP BY customer_email
            )
            SELECT
                COUNT(DISTINCT CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.today_d
                                     AND so.created_at = f.first_at
                                    THEN so.customer_email END)            AS new_today,
                COUNT(DISTINCT CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.today_d
                                     AND so.created_at > f.first_at
                                    THEN so.customer_email END)            AS returning_today,
                COUNT(DISTINCT CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.yesterday_d
                                     AND so.created_at = f.first_at
                                    THEN so.customer_email END)            AS new_yesterday,
                COUNT(DISTINCT CASE WHEN (so.created_at AT TIME ZONE :tz)::date = p.yesterday_d
                                     AND so.created_at > f.first_at
                                    THEN so.customer_email END)            AS returning_yesterday
            FROM shop_orders so
            JOIN firsts f USING (customer_email), p
            WHERE so.shop_domain = :shop AND so.currency = :currency
        """),
        {"shop": shop, "currency": currency, "tz": tz},
    ).fetchone()
    if row is None:
        return {"new_today": 0, "returning_today": 0,
                "new_yesterday": 0, "returning_yesterday": 0}
    return {
        "new_today": int(row[0] or 0),
        "returning_today": int(row[1] or 0),
        "new_yesterday": int(row[2] or 0),
        "returning_yesterday": int(row[3] or 0),
    }


def _query_top_sellers_today(db: Session, shop: str, tz: str, limit: int = 5) -> list[dict]:
    """Top products by revenue from today's order line_items."""
    rows = db.execute(
        text("""
            SELECT
                item->>'title'                                            AS product_title,
                SUM((item->>'price')::numeric * (item->>'quantity')::int) AS revenue,
                SUM((item->>'quantity')::int)                             AS units_sold
            FROM shop_orders so,
                 jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) AS item
            WHERE so.shop_domain = :shop
              AND (so.created_at AT TIME ZONE :tz)::date = (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date
              AND item->>'title' IS NOT NULL
              AND item->>'price' IS NOT NULL
              AND item->>'quantity' IS NOT NULL
            GROUP BY item->>'title'
            ORDER BY revenue DESC
            LIMIT :lim
        """),
        {"shop": shop, "tz": tz, "lim": limit},
    ).fetchall()
    return [
        {
            "product_title": r[0],
            "revenue": round(float(r[1] or 0), 2),
            "units_sold": int(r[2] or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/today-snapshot",
    response_model=TodaySnapshotResponse,
    response_model_exclude_none=False,
)
def get_today_snapshot(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Six base KPIs for today + yesterday + delta — the day-1 retrospective
    pulse every cheap Shopify SaaS shows. Cached 60s, keyed by date so it
    auto-rotates at midnight and survives worker restarts.
    """
    currency = get_shop_currency(db, shop) or "USD"
    tz = get_shop_timezone(db, shop) or "UTC"
    # today_iso MUST come from the same tz boundary used by the SQL
    # queries below — using server-local UTC (date.today()) would
    # mis-align the cache key with the query bucket whenever the
    # merchant's tz differs from UTC at the date boundary, returning
    # stale data across midnight in their local time. Source the date
    # from the same `CURRENT_TIMESTAMP AT TIME ZONE :tz` expression
    # the query layer uses.
    today_iso_row = db.execute(
        text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date AS d"),
        {"tz": tz},
    ).fetchone()
    today_iso = str(today_iso_row[0]) if today_iso_row else ""

    cache_key = f"hs:today_snapshot:v1:{shop}:{today_iso}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        orders = _query_orders(db, shop, currency, tz)
        sessions = _query_sessions(db, shop, tz)
        nr = _query_new_returning(db, shop, currency, tz)
        top_sellers = _query_top_sellers_today(db, shop, tz, limit=5)
    except Exception as exc:
        log.warning("today_snapshot: shop=%s: %s", shop, exc)
        orders = {k: 0 for k in ("rev_today", "orders_today", "aov_today",
                                 "rev_yesterday", "orders_yesterday", "aov_yesterday")}
        sessions = {"sess_today": 0, "sess_yesterday": 0}
        nr = {"new_today": 0, "returning_today": 0,
              "new_yesterday": 0, "returning_yesterday": 0}
        top_sellers = []

    cvr_today = _conversion_pct(orders["orders_today"], sessions["sess_today"])
    cvr_yesterday = _conversion_pct(orders["orders_yesterday"], sessions["sess_yesterday"])
    cvr_delta = (
        round(cvr_today - cvr_yesterday, 2)
        if cvr_today is not None and cvr_yesterday is not None
        else None
    )

    payload = {
        "currency": currency,
        "timezone": tz,
        "today_iso": today_iso,
        "has_data": (
            orders["orders_today"] > 0
            or orders["orders_yesterday"] > 0
            or sessions["sess_today"] > 0
            or sessions["sess_yesterday"] > 0
        ),
        "today": {
            "revenue": orders["rev_today"],
            "orders": orders["orders_today"],
            "aov": orders["aov_today"],
            "sessions": sessions["sess_today"],
            "conversion_rate_pct": cvr_today,
            "new_customers": nr["new_today"],
            "returning_customers": nr["returning_today"],
        },
        "yesterday": {
            "revenue": orders["rev_yesterday"],
            "orders": orders["orders_yesterday"],
            "aov": orders["aov_yesterday"],
            "sessions": sessions["sess_yesterday"],
            "conversion_rate_pct": cvr_yesterday,
            "new_customers": nr["new_yesterday"],
            "returning_customers": nr["returning_yesterday"],
        },
        "deltas": {
            "revenue_pct": _delta_pct(orders["rev_today"], orders["rev_yesterday"]),
            "orders_pct": _delta_pct(orders["orders_today"], orders["orders_yesterday"]),
            "aov_pct": _delta_pct(orders["aov_today"], orders["aov_yesterday"]),
            "sessions_pct": _delta_pct(sessions["sess_today"], sessions["sess_yesterday"]),
            "conversion_rate_pct_delta": cvr_delta,
        },
        "top_sellers_today": top_sellers,
    }
    cache_set(cache_key, payload, 60)
    return payload
