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
                         jsonb_array_elements(CASE WHEN jsonb_typeof(so.line_items) = 'array' THEN so.line_items ELSE '[]'::jsonb END) li
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


# ============================================================================
# Cohort by dimension — Gap #8 close (brutal $0-70 audit + parity doctrine)
# ============================================================================
#
# Lifetimely ships "cohort by acquisition channel/first-product/discount" at
# $39 entry tier. Per founder doctrine 2026-04-27: every $0-60 competitor
# feature → we build it, with clarity + accuracy + unique-feature on top.
#
# Architecture: SIBLING of get_monthly_cohorts (does NOT modify it). Reuses
# the customer_orders aggregation pattern + adds a per-customer dimension
# value lookup. Returns a flat list grouped by dim_value × cohort_month.
#
# Dimensions supported:
#   - first_channel  — customer's FIRST acquisition channel
#                      (visitor_purchase_sessions.last_source on first order)
#   - first_product  — title of customer's FIRST line-item product
#   - first_discount — first discount code used (or "(none)")
#
# Differentiator on top (parity doctrine §3 — unique-feature tag):
#   `best_vs_worst` field surfaces plain-language insight ("Customers
#   acquired via X have N% higher repeat rate than Y") — reading-grade
#   single-line takeaway no competitor ships at this price.

_VALID_COHORT_DIMS = ("first_channel", "first_product", "first_discount")


# ---------------------------------------------------------------------------
# Cohort-by-dimension building blocks
# ---------------------------------------------------------------------------
# Refactor 2026-05-12 (A3 medium close): 277-LOC god function → composer +
# 4 pure helpers + 3 SQL templates dim-dispatched. Identical contract
# (signature, return shape, semantics) verified by 13 black-box tests.

_DIM_LABELS: dict[str, str] = {
    "first_channel":  "channel",
    "first_product":  "first product",
    "first_discount": "discount code",
}

_DIM_QUERIES: dict[str, str] = {
    "first_channel": """
        SELECT
            so.customer_id,
            so.customer_email,
            so.created_at,
            CAST(so.total_price AS FLOAT) AS total_price,
            COALESCE(NULLIF(vps.last_source, ''), '(direct/unknown)') AS dim_value
        FROM shop_orders so
        LEFT JOIN visitor_purchase_sessions vps
          ON vps.shop_domain = so.shop_domain
         AND vps.shopify_order_id = so.shopify_order_id
        WHERE so.shop_domain = :shop AND so.created_at >= :since
        ORDER BY so.created_at ASC
    """,
    "first_product": """
        SELECT
            so.customer_id,
            so.customer_email,
            so.created_at,
            CAST(so.total_price AS FLOAT) AS total_price,
            COALESCE(
                NULLIF((so.line_items->0->>'title'), ''),
                '(unknown)'
            ) AS dim_value
        FROM shop_orders so
        WHERE so.shop_domain = :shop AND so.created_at >= :since
        ORDER BY so.created_at ASC
    """,
    "first_discount": """
        SELECT
            so.customer_id,
            so.customer_email,
            so.created_at,
            CAST(so.total_price AS FLOAT) AS total_price,
            -- Defensive: discount_codes JSONB may hold null, an
            -- array, or (rare malformed historical data) a JSON
            -- scalar. Guard with jsonb_typeof BEFORE calling
            -- jsonb_array_length to avoid "cannot get array length
            -- of a scalar" PostgreSQL errors.
            CASE
                WHEN so.discount_codes IS NULL THEN '(none)'
                WHEN jsonb_typeof(so.discount_codes) <> 'array' THEN '(none)'
                WHEN jsonb_array_length(so.discount_codes) = 0 THEN '(none)'
                ELSE COALESCE(
                    NULLIF((so.discount_codes->>0), ''),
                    '(none)'
                )
            END AS dim_value
        FROM shop_orders so
        WHERE so.shop_domain = :shop AND so.created_at >= :since
        ORDER BY so.created_at ASC
    """,
}


def _fetch_orders_for_dim(db, shop_domain, since, dim):
    """Execute the dim-specific query. Returns list of rows, or None on error."""
    try:
        return db.execute(
            text(_DIM_QUERIES[dim]),
            {"shop": shop_domain, "since": since},
        ).fetchall()
    except Exception as exc:
        log.error(
            "ltv_engine.cohorts_by_dimension: query failed shop=%s dim=%s: %s",
            shop_domain, dim, exc,
        )
        return None


def _aggregate_customer_timelines(rows):
    """
    Build per-customer order timeline + first-order dim attribution.
    Rows arrive ordered by created_at ASC; first-order dim wins.

    Returns: (customer_orders, customer_first_dim, customer_first_order_ts,
              identifiable_orders).
    """
    customer_orders: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    customer_first_dim: dict[str, str] = {}
    customer_first_order_ts: dict[str, datetime] = {}
    identifiable_orders = 0

    for row in rows:
        key = _customer_key(row[0], row[1])
        if key is None:
            continue
        created_at = row[2]
        price = float(row[3] or 0)
        dim_value = row[4] or "(unknown)"
        identifiable_orders += 1
        customer_orders[key].append((created_at, price))
        if key not in customer_first_dim:
            customer_first_dim[key] = dim_value
            customer_first_order_ts[key] = created_at

    return customer_orders, customer_first_dim, customer_first_order_ts, identifiable_orders


def _build_bucket(dim_value, members, customer_orders, customer_first_order_ts) -> dict:
    """Compute size + repeat_rate + revenue + cohort_months for one dim bucket."""
    size = len(members)
    all_orders = [o for ck in members for o in customer_orders[ck]]
    revenue_total = sum(p for _, p in all_orders)
    orders_total = len(all_orders)
    repeat_count = sum(
        1 for ck in members
        if len({dt.strftime("%Y-%m") for dt, _ in customer_orders[ck]}) >= 2
    )

    # Per-cohort-month breakdown WITHIN this dim bucket
    cohort_members_in_dim: dict[str, list[str]] = defaultdict(list)
    for ck in members:
        month_str = customer_first_order_ts[ck].strftime("%Y-%m")
        cohort_members_in_dim[month_str].append(ck)

    cohort_months_out = []
    for month_str in sorted(cohort_members_in_dim.keys(), reverse=True):
        cm = cohort_members_in_dim[month_str]
        csize = len(cm)
        crevenue = sum(p for ck in cm for _, p in customer_orders[ck])
        crepeat = sum(
            1 for ck in cm
            if len({dt.strftime("%Y-%m") for dt, _ in customer_orders[ck]}) >= 2
        )
        cohort_months_out.append({
            "cohort_month": month_str,
            "size": csize,
            "revenue_total": round(crevenue, 2),
            "repeat_rate": round(crepeat / csize, 4) if csize > 0 else 0.0,
        })

    return {
        "dim_value": str(dim_value)[:128],
        "size": size,
        "repeat_rate": round(repeat_count / size, 4) if size > 0 else 0.0,
        "revenue_per_customer": round(revenue_total / size, 2) if size > 0 else 0.0,
        "orders_per_customer": round(orders_total / size, 2) if size > 0 else 0.0,
        "cohort_months": cohort_months_out,
    }


_BVW_DEFAULT: dict = {
    "best_dim_value": None,
    "worst_dim_value": None,
    "best_repeat_rate": None,
    "worst_repeat_rate": None,
    "lift_pct": None,
    "insight": "Need at least 2 segments with 5+ customers each "
               "for a reliable best-vs-worst comparison.",
}


def _compute_best_vs_worst(buckets: list[dict], dim: str) -> dict:
    """
    Derive plain-language insight from best vs worst bucket.
    Requires ≥2 buckets with size ≥5 (cold-start guard against noise).
    """
    bvw_buckets = [b for b in buckets if b["size"] >= 5]
    if len(bvw_buckets) < 2:
        return dict(_BVW_DEFAULT)

    best = max(bvw_buckets, key=lambda b: b["repeat_rate"])
    worst = min(bvw_buckets, key=lambda b: b["repeat_rate"])
    if best["dim_value"] == worst["dim_value"]:
        return dict(_BVW_DEFAULT)

    wr, br = worst["repeat_rate"], best["repeat_rate"]
    lift_pct = round(((br - wr) / wr) * 100.0, 1) if wr > 0 else None
    dim_label = _DIM_LABELS[dim]

    if lift_pct is not None and lift_pct >= 5:
        insight = (
            f"Customers acquired via {best['dim_value']} have a "
            f"{br * 100:.0f}% repeat rate — {lift_pct:.0f}% higher "
            f"than {worst['dim_value']} ({wr * 100:.0f}%). Lean "
            f"into the {dim_label} pulling these customers."
        )
    else:
        insight = (
            f"Repeat rate is similar across {dim_label} buckets "
            f"({worst['dim_value']}: {wr * 100:.0f}% vs "
            f"{best['dim_value']}: {br * 100:.0f}%). Acquisition "
            f"channel isn't a meaningful retention lever yet."
        )

    return {
        "best_dim_value": best["dim_value"],
        "worst_dim_value": worst["dim_value"],
        "best_repeat_rate": br,
        "worst_repeat_rate": wr,
        "lift_pct": lift_pct,
        "insight": insight,
    }


def get_cohorts_by_dimension(
    db: Session,
    shop_domain: str,
    *,
    dim: str,
    months: int = 6,
    limit_dim_values: int = 8,
) -> dict:
    """Cohort retention sliced by a customer-attribute dimension.

    Args:
        dim: one of first_channel / first_product / first_discount
        months: rolling acquisition window (1-12)
        limit_dim_values: max dim values returned (1-20). Top-N by size.

    Returns:
        {
            dim, window_months, generated_at,
            customer_coverage: {total_orders, identifiable_orders, ...},
            buckets: [
                {dim_value, size, repeat_rate, revenue_per_customer,
                 orders_per_customer,
                 cohort_months: [{cohort_month, size, revenue_total,
                                  repeat_rate}]},
                ...
            ],
            best_vs_worst: {best_dim_value, worst_dim_value,
                            best_repeat_rate, worst_repeat_rate,
                            lift_pct, insight},
        }

    Refactored 2026-05-12 (A3 medium close): 277-LOC god function → 30-LOC
    composer + 4 pure helpers + 3 SQL templates; 13 black-box tests verify
    identical contract.
    """
    from datetime import datetime, timedelta as _td, timezone as _tzc

    if dim not in _VALID_COHORT_DIMS:
        return _empty_dim_cohort_response(dim, months)

    months = max(1, min(months, 12))
    limit_dim_values = max(1, min(limit_dim_values, 20))
    now = datetime.now(_tzc.utc).replace(tzinfo=None)
    since = now - _td(days=months * 31)

    rows = _fetch_orders_for_dim(db, shop_domain, since, dim)
    if rows is None or not rows:
        return _empty_dim_cohort_response(dim, months)

    total_orders = len(rows)
    customer_orders, customer_first_dim, customer_first_order_ts, identifiable_orders = (
        _aggregate_customer_timelines(rows)
    )
    if not customer_orders:
        return _empty_dim_cohort_response(dim, months, total_orders=total_orders)

    # Bucket by dim_value, then keep top-N by size
    by_dim: dict[str, list[str]] = defaultdict(list)
    for cust_key, dv in customer_first_dim.items():
        by_dim[dv].append(cust_key)
    sorted_dims = sorted(by_dim.items(), key=lambda x: -len(x[1]))[:limit_dim_values]

    buckets = [
        _build_bucket(dim_value, members, customer_orders, customer_first_order_ts)
        for dim_value, members in sorted_dims
        if len(members) > 0
    ]

    return {
        "dim": dim,
        "window_months": months,
        "generated_at": now.isoformat() + "Z",
        "customer_coverage": {
            "total_orders": total_orders,
            "identifiable_orders": identifiable_orders,
            "unidentifiable_orders": total_orders - identifiable_orders,
            "coverage_rate": round(identifiable_orders / total_orders, 3)
                if total_orders > 0 else 0.0,
        },
        "buckets": buckets,
        "best_vs_worst": _compute_best_vs_worst(buckets, dim),
    }


def _empty_dim_cohort_response(dim, months, total_orders=0):
    from datetime import datetime, timezone as _tzc
    return {
        "dim": dim,
        "window_months": months,
        "generated_at": datetime.now(_tzc.utc).replace(tzinfo=None).isoformat() + "Z",
        "customer_coverage": {
            "total_orders": total_orders,
            "identifiable_orders": 0,
            "unidentifiable_orders": total_orders,
            "coverage_rate": 0.0,
        },
        "buckets": [],
        "best_vs_worst": {
            "best_dim_value": None,
            "worst_dim_value": None,
            "best_repeat_rate": None,
            "worst_repeat_rate": None,
            "lift_pct": None,
            "insight": "No data yet. Cohort breakdown surfaces once "
                       "merchants accumulate identifiable customers.",
        },
    }
