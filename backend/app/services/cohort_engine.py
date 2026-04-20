"""
cohort_engine.py — Weekly cohort retention analysis.

Groups customers by their first purchase week and measures repurchase
rates over subsequent weeks.  Uses shop_orders from the Shopify webhook.

This is a fundamental retention analytics capability that no Shopify
behavioral tool currently provides alongside on-site intent data.

Cohort model
------------
- Cohort = week of first purchase (ISO week, Monday anchor)
- Retention = % of cohort who made ANY subsequent purchase in week N
- Revenue = total revenue from cohort over the measurement window

This is simple, honest cohort analysis — no prediction, no smoothing,
no imputed data.  Every number comes from real shop_orders rows.

Public interface
----------------
    get_cohort_retention(db, shop_domain, weeks=12) -> dict
        Returns the full cohort retention matrix.

    get_cohort_summary(db, shop_domain) -> dict
        Returns high-level retention summary (best cohort, avg retention).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def get_cohort_retention(
    db: Session,
    shop_domain: str,
    weeks: int = 12,
) -> dict:
    """
    Compute weekly cohort retention matrix.

    Returns:
        {
            "window_weeks": int,
            "generated_at": str,
            "cohorts": [
                {
                    "cohort_week":  str,     # e.g. "2025-W01"
                    "cohort_start": str,     # Monday ISO date
                    "size":         int,     # unique customers in cohort
                    "revenue_total": float,
                    "retention": {
                        "week_1": float,  # % of cohort who repurchased in week 1
                        "week_2": float,
                        ...
                        "week_N": float,
                    },
                },
                ...
            ],
            "avg_week_1_retention": float,
            "avg_week_4_retention": float,
            "best_cohort": str | None,
        }
    """
    weeks = max(4, min(weeks, 26))
    since_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(weeks=weeks + 1)

    try:
        # Step 1: Get all orders with customer email in the window
        rows = db.execute(
            text("""
                SELECT
                    customer_email,
                    created_at,
                    CAST(total_price AS FLOAT) AS total_price
                FROM shop_orders
                WHERE shop_domain     = :shop
                  AND customer_email IS NOT NULL
                  AND customer_email != ''
                  AND created_at     >= :since_date
                ORDER BY customer_email, created_at
            """),
            {"shop": shop_domain, "since_date": since_date},
        ).fetchall()

    except Exception as exc:
        log.error("cohort_engine: query failed shop=%s: %s", shop_domain, exc)
        return _empty_response(weeks)

    if not rows:
        return _empty_response(weeks)

    # Step 2: Build per-customer order timeline
    customer_orders: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        email = str(row[0])
        created_at = row[1]
        price = float(row[2] or 0)
        if email not in customer_orders:
            customer_orders[email] = []
        customer_orders[email].append((created_at, price))

    # Step 3: Assign each customer to their first-purchase cohort week
    # Cohort key = "YYYY-WNN" (ISO week)
    cohort_customers: dict[str, list[str]] = {}
    customer_first_purchase: dict[str, datetime] = {}

    for email, orders in customer_orders.items():
        first_purchase = min(o[0] for o in orders)
        customer_first_purchase[email] = first_purchase
        # ISO week: monday of the week
        monday = first_purchase - timedelta(days=first_purchase.weekday())
        week_key = monday.strftime("%Y-W%V")
        if week_key not in cohort_customers:
            cohort_customers[week_key] = []
        cohort_customers[week_key].append(email)

    # Step 4: Build retention matrix per cohort
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cohorts = []

    for week_key, emails in sorted(cohort_customers.items(), reverse=True):
        cohort_size = len(emails)
        if cohort_size == 0:
            continue

        # Monday of this cohort week
        try:
            year_str, week_str = week_key.split("-W")
            cohort_start = datetime.strptime(f"{year_str}-W{week_str}-1", "%Y-W%W-%w")
        except Exception:
            continue

        # Calculate total revenue for the cohort
        revenue_total = sum(
            p for email in emails
            for _, p in customer_orders.get(email, [])
        )

        # Compute retention per subsequent week
        retention = {}
        max_measurable_weeks = min(
            weeks,
            max(1, int((now - cohort_start).days // 7)),
        )

        for w in range(1, max_measurable_weeks + 1):
            week_start = cohort_start + timedelta(weeks=w)
            week_end   = week_start + timedelta(weeks=1)

            repurchasers = 0
            for email in emails:
                orders = customer_orders.get(email, [])
                # Count as retained if they purchased AFTER cohort week in this week
                had_repeat = any(
                    week_start <= o[0] < week_end
                    for o in orders
                    if o[0] > cohort_start + timedelta(weeks=1)
                )
                if had_repeat:
                    repurchasers += 1

            retention[f"week_{w}"] = round(repurchasers / cohort_size, 4)

        cohorts.append({
            "cohort_week":    week_key,
            "cohort_start":   cohort_start.strftime("%Y-%m-%d"),
            "size":           cohort_size,
            "revenue_total":  round(revenue_total, 2),
            "retention":      retention,
        })

    if not cohorts:
        return _empty_response(weeks)

    # Step 5: Summary stats
    week1_rates = [
        c["retention"].get("week_1", 0)
        for c in cohorts
        if "week_1" in c["retention"]
    ]
    week4_rates = [
        c["retention"].get("week_4", 0)
        for c in cohorts
        if "week_4" in c["retention"]
    ]

    avg_week1 = round(sum(week1_rates) / len(week1_rates), 4) if week1_rates else 0.0
    avg_week4 = round(sum(week4_rates) / len(week4_rates), 4) if week4_rates else 0.0

    best_cohort = None
    if cohorts:
        best = max(cohorts, key=lambda c: c["retention"].get("week_4", c["retention"].get("week_1", 0)))
        best_cohort = best["cohort_week"]

    return {
        "window_weeks":          weeks,
        "generated_at":          datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "cohorts":               cohorts[:weeks],
        "avg_week_1_retention":  avg_week1,
        "avg_week_4_retention":  avg_week4,
        "best_cohort":           best_cohort,
        "total_customers":       len(customer_orders),
    }


def get_cohort_summary(
    db: Session,
    shop_domain: str,
) -> dict:
    """
    High-level retention summary for the dashboard banner.

    Returns:
        {
            "avg_week_1_retention": float,
            "avg_week_4_retention": float,
            "total_customers":      int,
            "cohorts_measured":     int,
            "best_cohort":          str | None,
        }
    """
    # Strada 4 (dominate): extend the summary to 26 weeks and surface
    # week-8 / week-12 / week-26 averages in addition to the headline
    # week-1 / week-4. Peel's specialty is deep retention — we now
    # match their depth at the top-level view, keeping the per-cohort
    # matrix as the shared drill-down.
    try:
        full = get_cohort_retention(db, shop_domain, weeks=26)

        def _avg_for_window(key: str) -> float:
            rates = [c["retention"].get(key, 0) for c in full["cohorts"] if key in c["retention"]]
            return round(sum(rates) / len(rates), 4) if rates else 0.0

        return {
            "avg_week_1_retention":  full["avg_week_1_retention"],
            "avg_week_4_retention":  full["avg_week_4_retention"],
            "avg_week_8_retention":  _avg_for_window("week_8"),
            "avg_week_12_retention": _avg_for_window("week_12"),
            "avg_week_26_retention": _avg_for_window("week_26"),
            "total_customers":       full["total_customers"],
            "cohorts_measured":      len(full["cohorts"]),
            "best_cohort":           full["best_cohort"],
        }
    except Exception as exc:
        log.error("cohort_engine: summary failed shop=%s: %s", shop_domain, exc)
        return {
            "avg_week_1_retention":  0.0,
            "avg_week_4_retention":  0.0,
            "avg_week_8_retention":  0.0,
            "avg_week_12_retention": 0.0,
            "avg_week_26_retention": 0.0,
            "total_customers":       0,
            "cohorts_measured":      0,
            "best_cohort":           None,
        }


def _empty_response(weeks: int) -> dict:
    return {
        "window_weeks":          weeks,
        "generated_at":          datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "cohorts":               [],
        "avg_week_1_retention":  0.0,
        "avg_week_4_retention":  0.0,
        "best_cohort":           None,
        "total_customers":       0,
    }
