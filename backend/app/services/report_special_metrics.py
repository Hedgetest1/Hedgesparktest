"""report_special_metrics.py — Gap #1 strict 10/10 closure.

Dedicated SQL aggregations for the 5 metrics that previously fell
back to revenue: repeat_rate, customer_ltv, conversion_rate,
revenue_at_risk, survey_response_top.

Each function returns a uniform shape used by the report executor:
  {"label": str, "value": float, "supports_time_bucket": bool}

For dimensions other than scalar / time, the executor surfaces a
calm merchant-friendly note explaining that the breakdown isn't
available for that combination yet.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("report_special_metrics")


def repeat_rate(db: Session, shop: str, start: datetime, end: datetime) -> float:
    """% of customers in the window with ≥2 orders."""
    row = db.execute(text(
        """
        WITH per_customer AS (
            SELECT customer_email, COUNT(*) AS orders_in_window
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at BETWEEN :start AND :end
              AND customer_email IS NOT NULL
            GROUP BY customer_email
        )
        SELECT
            COALESCE(
                COUNT(*) FILTER (WHERE orders_in_window >= 2)::float
                / NULLIF(COUNT(*), 0)::float * 100.0,
                0.0
            ) AS pct
        FROM per_customer
        """
    ), {"shop": shop, "start": start, "end": end}).fetchone()
    return float(row.pct or 0.0) if row else 0.0


def customer_ltv(db: Session, shop: str, start: datetime, end: datetime) -> float:
    """Avg revenue per customer in the window (window-LTV approximation).

    Currency-scoped to the merchant's primary currency so multi-currency
    orders don't naively sum across symbols (audit_data_truth gate).
    """
    row = db.execute(text(
        """
        SELECT
            COALESCE(
                SUM(total_price)
                / NULLIF(COUNT(DISTINCT customer_email), 0),
                0.0
            ) AS ltv
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at BETWEEN :start AND :end
          AND customer_email IS NOT NULL
          AND currency = COALESCE(
              (SELECT primary_currency FROM merchants WHERE shop_domain = :shop LIMIT 1),
              currency
          )
        """
    ), {"shop": shop, "start": start, "end": end}).fetchone()
    return float(row.ltv or 0.0) if row else 0.0


def conversion_rate(db: Session, shop: str, start: datetime, end: datetime) -> float:
    """Orders / distinct visitors that touched the store in the window.

    Visitors come from `visitor_purchase_sessions.visitor_id` (the
    canonical identity for a buying journey). Falls through to 0.0
    when there's no traffic data.
    """
    row = db.execute(text(
        """
        WITH orders AS (
            SELECT COUNT(DISTINCT shopify_order_id)::float AS n
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at BETWEEN :start AND :end
        ),
        visitors AS (
            SELECT COUNT(DISTINCT visitor_id)::float AS n
            FROM visitor_purchase_sessions
            WHERE shop_domain = :shop
              AND confirmed_at BETWEEN :start AND :end
        )
        SELECT
            COALESCE(
                (SELECT n FROM orders) / NULLIF((SELECT n FROM visitors), 0) * 100.0,
                0.0
            ) AS pct
        """
    ), {"shop": shop, "start": start, "end": end}).fetchone()
    return float(row.pct or 0.0) if row else 0.0


def revenue_at_risk(db: Session, shop: str, start: datetime, end: datetime) -> float:
    """Read from the existing RARS pipeline (single scalar — total
    €/£/$ at risk right now). The window arguments are ignored
    because RARS is a 'right-now' metric, not a window aggregate."""
    try:
        from app.services.revenue_at_risk import get_revenue_at_risk
        report = get_revenue_at_risk(db, shop)
        if not isinstance(report, dict):
            return 0.0
        return float(report.get("total_at_risk_eur") or 0.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("revenue_at_risk RARS lookup failed: %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("report_special_metrics.rars")
        return 0.0


def survey_response_top(
    db: Session, shop: str, start: datetime, end: datetime
) -> dict[str, Any]:
    """Most common survey answer in the window. Returns
    {"label": <choice>, "count": <n>} or {"label": "(no answers)", "count": 0}.
    """
    row = db.execute(text(
        """
        SELECT answer_choice, COUNT(*)::int AS n
        FROM survey_responses
        WHERE shop_domain = :shop
          AND created_at BETWEEN :start AND :end
          AND answer_choice IS NOT NULL
        GROUP BY answer_choice
        ORDER BY n DESC
        LIMIT 1
        """
    ), {"shop": shop, "start": start, "end": end}).fetchone()
    if not row:
        return {"label": "(no answers)", "count": 0}
    return {"label": str(row.answer_choice), "count": int(row.n)}


# ---------------------------------------------------------------------------
# Time-bucket support — for metric × time dimension reports
# ---------------------------------------------------------------------------


def _time_bucket_clause(grain: str) -> str:
    if grain == "day":
        return "to_char(created_at, 'YYYY-MM-DD')"
    if grain == "week":
        return "to_char(created_at, 'IYYY-IW')"
    return "to_char(created_at, 'YYYY-MM')"


def repeat_rate_by_time(
    db: Session, shop: str, start: datetime, end: datetime, grain: str
) -> list[dict[str, Any]]:
    bucket = _time_bucket_clause(grain)
    # elite-hardening-allowed: {bucket} from _time_bucket_clause which returns one of 3 hardcoded `to_char(...)` strings selected by a whitelisted `grain` ∈ {"day", "week", "month"}
    rows = db.execute(text(
        f"""
        WITH per_bucket AS (
            SELECT {bucket} AS bucket,
                   customer_email,
                   COUNT(*) AS n
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at BETWEEN :start AND :end
              AND customer_email IS NOT NULL
            GROUP BY {bucket}, customer_email
        )
        SELECT bucket,
               COALESCE(
                   COUNT(*) FILTER (WHERE n >= 2)::float
                   / NULLIF(COUNT(*), 0)::float * 100.0,
                   0.0
               ) AS pct
        FROM per_bucket
        GROUP BY bucket
        ORDER BY bucket
        """
    ), {"shop": shop, "start": start, "end": end}).fetchall()
    return [{"label": r.bucket, "value": float(r.pct or 0.0)} for r in rows]


def customer_ltv_by_time(
    db: Session, shop: str, start: datetime, end: datetime, grain: str
) -> list[dict[str, Any]]:
    bucket = _time_bucket_clause(grain)
    # elite-hardening-allowed: {bucket} from _time_bucket_clause which returns one of 3 hardcoded `to_char(...)` strings selected by a whitelisted `grain` ∈ {"day", "week", "month"}
    rows = db.execute(text(
        f"""
        SELECT {bucket} AS bucket,
               COALESCE(
                   SUM(total_price)
                   / NULLIF(COUNT(DISTINCT customer_email), 0),
                   0.0
               ) AS ltv
        FROM shop_orders
        WHERE shop_domain = :shop
          AND created_at BETWEEN :start AND :end
          AND customer_email IS NOT NULL
          AND currency = COALESCE(
              (SELECT primary_currency FROM merchants WHERE shop_domain = :shop LIMIT 1),
              currency
          )
        GROUP BY {bucket}
        ORDER BY bucket
        """
    ), {"shop": shop, "start": start, "end": end}).fetchall()
    return [{"label": r.bucket, "value": float(r.ltv or 0.0)} for r in rows]


_SPECIAL_METRIC_NAMES = frozenset({
    "repeat_rate",
    "customer_ltv",
    "conversion_rate",
    "revenue_at_risk",
    "survey_response_top",
})


def is_special(metric: str) -> bool:
    return metric in _SPECIAL_METRIC_NAMES
