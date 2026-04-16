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

        # Repeat rate: fraction with orders in 2+ DISTINCT months
        # Industry standard: "repeat" means the customer returned in a
        # different month, not just placed 2 orders in the same session.
        repeat_count = sum(
            1 for ck in members
            if len({dt.strftime("%Y-%m") for dt, _ in customer_orders[ck]}) >= 2
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


def get_product_ltv_contribution(
    db: Session,
    shop_domain: str,
    limit: int = 20,
) -> dict:
    """
    Which products drive high-LTV customers?

    For each product: avg customer LTV of buyers, repeat rate, and whether
    the product is a "gateway" (first purchase) or "repeat" product.
    """
    from app.core.redis_client import cache_get, cache_set
    from app.services.revenue_metrics import get_shop_currency
    cache_key = f"hs:ltv:products:{shop_domain}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    currency = get_shop_currency(db, shop_domain)

    try:
        result = db.execute(
            text("""
                WITH customer_stats AS (
                    SELECT
                        COALESCE(customer_id, customer_email) AS cust_key,
                        COUNT(*) AS total_orders,
                        SUM(total_price) AS total_spend,
                        MIN(created_at) AS first_order_at
                    FROM shop_orders
                    WHERE shop_domain = :shop
                      AND (customer_id IS NOT NULL OR customer_email IS NOT NULL)
                      AND (:currency IS NULL OR currency = :currency)
                    GROUP BY cust_key
                ),
                product_buyers AS (
                    SELECT DISTINCT
                        COALESCE(so.customer_id, so.customer_email) AS cust_key,
                        li->>'title' AS product_title,
                        COALESCE(li->>'product_url', li->>'handle') AS product_key,
                        so.created_at AS order_date
                    FROM shop_orders so,
                         jsonb_array_elements(so.line_items) li
                    WHERE so.shop_domain = :shop
                      AND (so.customer_id IS NOT NULL OR so.customer_email IS NOT NULL)
                      AND (:currency IS NULL OR so.currency = :currency)
                )
                SELECT
                    pb.product_key,
                    MAX(pb.product_title) AS product_title,
                    COUNT(DISTINCT pb.cust_key) AS buyer_count,
                    AVG(cs.total_spend) AS avg_buyer_ltv,
                    AVG(cs.total_orders) AS avg_buyer_orders,
                    COUNT(*) FILTER (WHERE cs.total_orders >= 2)::float
                        / GREATEST(COUNT(DISTINCT pb.cust_key), 1) AS buyer_repeat_rate,
                    COUNT(*) FILTER (
                        WHERE pb.order_date = cs.first_order_at
                    )::float / GREATEST(COUNT(*), 1) AS gateway_rate
                FROM product_buyers pb
                INNER JOIN customer_stats cs ON cs.cust_key = pb.cust_key
                WHERE pb.product_key IS NOT NULL
                GROUP BY pb.product_key
                HAVING COUNT(DISTINCT pb.cust_key) >= 2
                ORDER BY avg_buyer_ltv DESC
                LIMIT :limit
            """),
            {"shop": shop_domain, "limit": limit, "currency": currency},
        )
        rows = result.fetchall()
    except Exception as exc:
        log.error("ltv_engine: product_ltv failed shop=%s: %s", shop_domain, exc)
        return {"shop_domain": shop_domain, "products": []}

    products = []
    for row in rows:
        products.append({
            "product": row.product_key,
            "title": row.product_title,
            "buyer_count": int(row.buyer_count),
            "avg_buyer_ltv": round(float(row.avg_buyer_ltv or 0), 2),
            "avg_buyer_orders": round(float(row.avg_buyer_orders or 0), 1),
            "buyer_repeat_rate": round(float(row.buyer_repeat_rate or 0), 4),
            "gateway_rate": round(float(row.gateway_rate or 0), 4),
            "is_gateway": float(row.gateway_rate or 0) > 0.5,
        })

    report = {"shop_domain": shop_domain, "products": products}
    cache_set(cache_key, report, 600)
    return report


def get_predicted_ltv(
    db: Session,
    shop_domain: str,
    limit: int = 50,
) -> dict:
    """
    Top customers with predicted 30-day and 12-month LTV.

    Uses recency-frequency heuristics (not ML) for prediction:
    - Recent + frequent = high probability of return
    - High AOV + recent = high predicted value
    """
    from app.core.redis_client import cache_get, cache_set
    from app.services.revenue_metrics import get_shop_currency
    cache_key = f"hs:ltv:predicted:{shop_domain}:{limit}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    currency = get_shop_currency(db, shop_domain)
    now = _now()
    try:
        result = db.execute(
            text("""
                SELECT
                    COALESCE(customer_id, customer_email) AS cust_key,
                    customer_email,
                    COUNT(*) AS total_orders,
                    SUM(total_price) AS total_spend,
                    MIN(created_at) AS first_order,
                    MAX(created_at) AS last_order,
                    EXTRACT(EPOCH FROM (:now - MAX(created_at))) / 86400.0
                        AS days_since_last
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND (customer_id IS NOT NULL OR customer_email IS NOT NULL)
                  AND (:currency IS NULL OR currency = :currency)
                GROUP BY cust_key, customer_email
                ORDER BY total_spend DESC
                LIMIT :limit
            """),
            {"shop": shop_domain, "now": now, "limit": limit, "currency": currency},
        )
        rows = result.fetchall()
    except Exception as exc:
        log.error("ltv_engine: predicted_ltv failed shop=%s: %s", shop_domain, exc)
        return {"shop_domain": shop_domain, "customers": [], "count": 0}

    customers = []
    for row in rows:
        days_since = float(row.days_since_last or 365)
        total_orders = int(row.total_orders)
        total_spend = float(row.total_spend or 0)
        aov = total_spend / max(total_orders, 1)

        # Recency-frequency probability model
        if total_orders >= 3 and days_since < 30:
            prob_30d = 0.60
        elif total_orders >= 2 and days_since < 60:
            prob_30d = 0.40
        elif total_orders >= 2 and days_since < 90:
            prob_30d = 0.25
        elif days_since < 30:
            prob_30d = 0.15
        else:
            prob_30d = 0.05

        predicted_30d = prob_30d * aov
        # 12-month: extrapolate from current rate
        months_active = max(1, (now - row.first_order).days / 30) if row.first_order else 1
        monthly_rate = total_orders / months_active
        predicted_12m = total_spend + (monthly_rate * aov * max(0, 12 - months_active))

        email = row.customer_email
        masked = None
        if email and "@" in email:
            local, domain = email.split("@", 1)
            masked = f"{local[0]}***@{domain}" if len(local) > 1 else f"*@{domain}"

        customers.append({
            "customer_key": row.cust_key,
            "email_hint": masked,
            "total_orders": total_orders,
            "total_spend": round(total_spend, 2),
            "aov": round(aov, 2),
            "days_since_last": round(days_since, 0),
            "repeat_probability_30d": round(prob_30d, 2),
            "predicted_30d_value": round(predicted_30d, 2),
            "predicted_12m_ltv": round(predicted_12m, 2),
        })

    report = {"shop_domain": shop_domain, "customers": customers, "count": len(customers)}
    cache_set(cache_key, report, 600)
    return report


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
