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
    now: datetime | None = None,
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
    # Injectable clock — the single "now" for the window, the
    # measurable-weeks cap, and generated_at. Defaults to wall-clock;
    # tests pass a fixed value so cohort math is DETERMINISTIC (the
    # prior reliance on datetime.now() made the retention test a
    # 1-in-7 weekday time bomb — see test_cohort_engine_composer).
    _now = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    since_date = _now - timedelta(weeks=weeks + 1)

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

    # Step 3: Assign each customer to their first-purchase cohort week.
    # Cohort key = "YYYY-WNN" (ISO week). We store the cohort's Monday
    # directly instead of round-tripping through strftime("%V") →
    # strptime("%W"): the two format codes don't share semantics
    # (%V is ISO 8601 week-number, %W is Monday-anchored Gregorian week,
    # which drift by a week around the new year). The direct Monday
    # datetime is the unambiguous source of truth for retention math.
    cohort_customers: dict[str, list[str]] = {}
    cohort_starts: dict[str, datetime] = {}
    customer_first_purchase: dict[str, datetime] = {}

    for email, orders in customer_orders.items():
        first_purchase = min(o[0] for o in orders)
        customer_first_purchase[email] = first_purchase
        # Monday of the cohort week, normalized to midnight so the
        # week boundaries line up regardless of intra-day purchase time.
        monday = (first_purchase - timedelta(days=first_purchase.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        week_key = monday.strftime("%Y-W%V")
        if week_key not in cohort_customers:
            cohort_customers[week_key] = []
            cohort_starts[week_key] = monday
        cohort_customers[week_key].append(email)

    # Step 4: Build retention matrix per cohort
    cohorts = []

    for week_key, emails in sorted(cohort_customers.items(), reverse=True):
        cohort_size = len(emails)
        if cohort_size == 0:
            continue

        cohort_start = cohort_starts[week_key]

        # Calculate total revenue for the cohort
        revenue_total = sum(
            p for email in emails
            for _, p in customer_orders.get(email, [])
        )

        # Compute retention per subsequent week.
        #
        # STRUCTURAL anchor = each customer's OWN first order, NOT the
        # cohort Monday. week_w counts a customer who placed any order
        # in [first + w*7d, first + (w+1)*7d) for w >= 1, where
        # w = (order_date - customer_first_order).days // 7.
        #
        # Why (the bug this replaces, 2026-05-18, independent audit +
        # the long-carried R-blocker, both vindicated): the prior code
        # measured every customer's repeats relative to the shared
        # `cohort_start` (the cohort's ISO-Monday). That carried the
        # customer's first-purchase WEEKDAY into the bucket offset, so
        # two customers with IDENTICAL repeat latency landed in
        # DIFFERENT retention weeks purely by which weekday they were
        # acquired (an 8-day-later repeat fell in week_1 or week_2 by
        # first-purchase weekday) — weekday-noisy, non-comparable
        # cross-cohort numbers shown to Lite merchants + the weekly
        # digest + Ask-HS. It also layered a redundant/off-by-one guard
        # (`o > cohort_start + 1wk`) that shifted week_1's lower edge.
        # Per-customer elapsed-week math removes the weekday term
        # entirely AND drops the guard (one bucket assignment, no
        # second lower bound). w==0 (a same-week repeat, days 0-6) is,
        # by the week-OVER-week retention definition, intentionally NOT
        # part of the subsequent-weeks curve (a distinct "same-week
        # repeat" metric, out of scope here — documented, no week_0
        # column = no API/UI change). The cohort Monday is retained
        # ONLY as the grouping LABEL (cohort_week / cohort_start),
        # which is correct and unchanged.
        retention = {}
        max_measurable_weeks = min(
            weeks,
            max(1, int((_now - cohort_start).days // 7)),
        )

        for w in range(1, max_measurable_weeks + 1):
            repurchasers = 0
            for email in emails:
                first = customer_first_purchase[email]
                if any(
                    ((o[0] - first).days // 7) == w
                    for o in customer_orders.get(email, [])
                    if o[0] > first
                ):
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
