"""
ltv_engine.py — LTV and monthly cohort analytics.

Extends the weekly cohort_engine with monthly cohort views and per-customer
lifetime value metrics.  Uses real shop_orders data only — no prediction,
no smoothing, no imputed data.

Customer identity resolution:
    1. customer_id (Shopify's permanent customer identifier) — preferred
    2. customer_email (falls back when customer_id is NULL)
    3. Orders with neither are excluded and reported as unidentifiable

Public interface
----------------
    get_monthly_cohorts(db, shop_domain, months=6) -> dict
        Monthly acquisition cohorts with cumulative revenue by age.

    get_ltv_summary(db, shop_domain, months=6) -> dict
        High-level LTV metrics: repeat rate, ARPC, orders/customer.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("ltv_engine")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _customer_key(customer_id, customer_email) -> str | None:
    """
    Resolve a deterministic customer identity key.
    Prefers customer_id (stable across email changes).
    Falls back to customer_email.
    Returns None if neither is available.
    """
    if customer_id:
        return f"id:{customer_id}"
    if customer_email:
        return f"email:{customer_email.lower().strip()}"
    return None


def get_monthly_cohorts(
    db: Session,
    shop_domain: str,
    months: int = 6,
) -> dict:
    """
    Compute monthly acquisition cohort analysis.

    Each cohort is defined by the month of the customer's FIRST order.
    For each cohort, we compute:
      - size: unique customers who first purchased in this month
      - revenue_total: total lifetime revenue from this cohort
      - cumulative_revenue: revenue accumulated at each month age (0, 1, 2, ...)
      - orders_per_customer: avg orders per customer in this cohort
      - revenue_per_customer: avg revenue per customer
      - repeat_rate: fraction of cohort who made 2+ orders ever

    Returns:
        {
            "window_months": int,
            "generated_at": str,
            "customer_coverage": {
                "total_orders": int,
                "identifiable_orders": int,
                "coverage_rate": float,
            },
            "cohorts": [
                {
                    "cohort_month": str,         # e.g. "2026-03"
                    "size": int,
                    "revenue_total": float,
                    "orders_total": int,
                    "orders_per_customer": float,
                    "revenue_per_customer": float,
                    "repeat_rate": float,
                    "cumulative_revenue": [       # revenue by month age
                        {"month_age": 0, "revenue": float, "customers_active": int},
                        {"month_age": 1, "revenue": float, "customers_active": int},
                        ...
                    ],
                }
            ],
            "overall": {
                "total_customers": int,
                "repeat_customers": int,
                "repeat_rate": float,
                "avg_orders_per_customer": float,
                "avg_revenue_per_customer": float,
            },
        }
    """
    months = max(1, min(months, 12))
    since_date = _now() - timedelta(days=months * 31)

    try:
        rows = db.execute(
            text("""
                SELECT
                    customer_id,
                    customer_email,
                    created_at,
                    CAST(total_price AS FLOAT) AS total_price
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= :since
                ORDER BY created_at ASC
            """),
            {"shop": shop_domain, "since": since_date},
        ).fetchall()
    except Exception as exc:
        log.error("ltv_engine: query failed shop=%s: %s", shop_domain, exc)
        return _empty_response(months)

    if not rows:
        return _empty_response(months)

    # Count coverage
    total_orders = len(rows)
    identifiable_orders = 0

    # Build per-customer order timeline
    # customer_key → [(created_at, total_price)]
    customer_orders: dict[str, list[tuple[datetime, float]]] = defaultdict(list)

    for row in rows:
        cust_id = row[0]      # customer_id (nullable)
        cust_email = row[1]   # customer_email (nullable)
        created_at = row[2]
        price = float(row[3] or 0)

        key = _customer_key(cust_id, cust_email)
        if key is None:
            continue  # unidentifiable — excluded honestly
        identifiable_orders += 1
        customer_orders[key].append((created_at, price))

    if not customer_orders:
        return _empty_response(months, total_orders=total_orders)

    # Assign each customer to their first-order month cohort
    # cohort_month → [customer_key, ...]
    cohort_members: dict[str, list[str]] = defaultdict(list)
    customer_first_month: dict[str, str] = {}

    for cust_key, orders in customer_orders.items():
        first_order = min(orders, key=lambda o: o[0])
        month_str = first_order[0].strftime("%Y-%m")
        customer_first_month[cust_key] = month_str
        cohort_members[month_str].append(cust_key)

    # Build cohort data
    now = _now()
    cohorts = []

    for month_str in sorted(cohort_members.keys(), reverse=True):
        members = cohort_members[month_str]
        size = len(members)
        if size == 0:
            continue

        # Parse cohort month start
        try:
            cohort_start = datetime.strptime(month_str + "-01", "%Y-%m-%d")
        except ValueError:
            continue

        # All orders from these customers (lifetime, not just in cohort month)
        all_orders = []
        for cust_key in members:
            all_orders.extend(customer_orders[cust_key])

        revenue_total = sum(p for _, p in all_orders)
        orders_total = len(all_orders)
        orders_per_customer = round(orders_total / size, 2)
        revenue_per_customer = round(revenue_total / size, 2)

        # Repeat rate: fraction with 2+ orders
        repeat_count = sum(
            1 for ck in members
            if len(customer_orders[ck]) >= 2
        )
        repeat_rate = round(repeat_count / size, 4) if size > 0 else 0.0

        # Cumulative revenue by month age
        # Month age 0 = cohort month, age 1 = next month, etc.
        max_age = min(months, max(1, int((now - cohort_start).days // 30)))
        cumulative_revenue = []
        running_total = 0.0

        for age in range(max_age + 1):
            age_start = _add_months(cohort_start, age)
            age_end = _add_months(cohort_start, age + 1)

            # Revenue from cohort members in this month
            month_revenue = 0.0
            customers_active = 0
            active_set: set[str] = set()

            for cust_key in members:
                for order_dt, order_price in customer_orders[cust_key]:
                    if age_start <= order_dt < age_end:
                        month_revenue += order_price
                        active_set.add(cust_key)

            customers_active = len(active_set)
            running_total += month_revenue

            cumulative_revenue.append({
                "month_age": age,
                "revenue": round(running_total, 2),
                "month_revenue": round(month_revenue, 2),
                "customers_active": customers_active,
            })

        cohorts.append({
            "cohort_month": month_str,
            "size": size,
            "revenue_total": round(revenue_total, 2),
            "orders_total": orders_total,
            "orders_per_customer": orders_per_customer,
            "revenue_per_customer": revenue_per_customer,
            "repeat_rate": repeat_rate,
            "cumulative_revenue": cumulative_revenue,
        })

    # Overall metrics
    total_customers = len(customer_orders)
    total_all_orders = sum(len(orders) for orders in customer_orders.values())
    total_revenue = sum(p for orders in customer_orders.values() for _, p in orders)
    repeat_customers = sum(1 for orders in customer_orders.values() if len(orders) >= 2)

    return {
        "window_months": months,
        "generated_at": _now().isoformat() + "Z",
        "customer_coverage": {
            "total_orders": total_orders,
            "identifiable_orders": identifiable_orders,
            "unidentifiable_orders": total_orders - identifiable_orders,
            "coverage_rate": round(identifiable_orders / total_orders, 3) if total_orders > 0 else 0.0,
        },
        "cohorts": cohorts[:months],
        "overall": {
            "total_customers": total_customers,
            "repeat_customers": repeat_customers,
            "repeat_rate": round(repeat_customers / total_customers, 4) if total_customers > 0 else 0.0,
            "avg_orders_per_customer": round(total_all_orders / total_customers, 2) if total_customers > 0 else 0.0,
            "avg_revenue_per_customer": round(total_revenue / total_customers, 2) if total_customers > 0 else 0.0,
        },
    }


def get_ltv_summary(db: Session, shop_domain: str) -> dict:
    """
    High-level LTV metrics for the Pro dashboard.

    Returns:
        {
            "total_customers": int,
            "repeat_rate": float,
            "avg_orders_per_customer": float,
            "avg_revenue_per_customer": float,
            "top_cohort_month": str | None,
            "customer_coverage_rate": float,
        }
    """
    try:
        full = get_monthly_cohorts(db, shop_domain, months=6)
        overall = full.get("overall", {})

        # Find cohort with highest revenue per customer
        top_cohort = None
        if full.get("cohorts"):
            best = max(full["cohorts"], key=lambda c: c.get("revenue_per_customer", 0))
            top_cohort = best["cohort_month"]

        return {
            "total_customers": overall.get("total_customers", 0),
            "repeat_rate": overall.get("repeat_rate", 0.0),
            "avg_orders_per_customer": overall.get("avg_orders_per_customer", 0.0),
            "avg_revenue_per_customer": overall.get("avg_revenue_per_customer", 0.0),
            "top_cohort_month": top_cohort,
            "customer_coverage_rate": full.get("customer_coverage", {}).get("coverage_rate", 0.0),
        }
    except Exception as exc:
        log.error("ltv_engine: summary failed shop=%s: %s", shop_domain, exc)
        return {
            "total_customers": 0,
            "repeat_rate": 0.0,
            "avg_orders_per_customer": 0.0,
            "avg_revenue_per_customer": 0.0,
            "top_cohort_month": None,
            "customer_coverage_rate": 0.0,
        }


def _add_months(dt: datetime, n: int) -> datetime:
    """Add n months to a datetime (simple, no external deps)."""
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, 28)  # safe for all months
    return dt.replace(year=year, month=month, day=day)


def _empty_response(months: int, total_orders: int = 0) -> dict:
    return {
        "window_months": months,
        "generated_at": _now().isoformat() + "Z",
        "customer_coverage": {
            "total_orders": total_orders,
            "identifiable_orders": 0,
            "unidentifiable_orders": total_orders,
            "coverage_rate": 0.0,
        },
        "cohorts": [],
        "overall": {
            "total_customers": 0,
            "repeat_customers": 0,
            "repeat_rate": 0.0,
            "avg_orders_per_customer": 0.0,
            "avg_revenue_per_customer": 0.0,
        },
    }
