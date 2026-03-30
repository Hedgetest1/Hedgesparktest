"""
cohorts.py — Cohort retention and LTV analytics (Pro only).

Weekly retention:
    GET /pro/cohorts?shop=&weeks=          — weekly cohort retention matrix
    GET /pro/cohorts/summary?shop=         — high-level retention stats

Monthly LTV:
    GET /pro/cohorts/monthly?shop=&months= — monthly acquisition cohorts with
                                              cumulative revenue, repeat rate,
                                              orders/customer, ARPC
    GET /pro/cohorts/ltv?shop=             — high-level LTV metrics

This is the feature that directly attacks Lifetimely / Peel on their core
territory.  The key advantage WishSpark will eventually have:

    "Cohorts with high behavioral engagement in month 0 have 2x LTV
    at month 6 compared to low-engagement cohorts."

Lifetimely structurally cannot show that because they have no behavioral data.
We do.  This is the foundation for that future positioning.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.cohort_engine import get_cohort_retention, get_cohort_summary
from app.services.ltv_engine import get_monthly_cohorts, get_ltv_summary

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/cohorts", tags=["cohorts"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("")
def get_cohorts(
    weeks: int = 12,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Full weekly cohort retention matrix.

    Groups customers by their first purchase week and measures repurchase
    rates over subsequent weeks.  Maximum window: 26 weeks.

    Returns:
        {
            "window_weeks": int,
            "cohorts": [
                {
                    "cohort_week":   str,       # e.g. "2025-W01"
                    "cohort_start":  str,       # Monday ISO date
                    "size":          int,
                    "revenue_total": float,
                    "retention": {
                        "week_1": float,  # retention rate at week 1
                        "week_4": float,
                        ...
                    }
                }
            ],
            "avg_week_1_retention": float,
            "avg_week_4_retention": float,
            "best_cohort": str | None,
            "total_customers": int,
        }
    """
    weeks = max(4, min(weeks, 26))
    return get_cohort_retention(db, shop, weeks=weeks)


@router.get("/summary")
def get_cohort_summary_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    High-level retention summary for the Pro dashboard.

    Returns:
        {
            "avg_week_1_retention": float,  # % who bought again within 1 week
            "avg_week_4_retention": float,  # % who bought again within 4 weeks
            "total_customers":      int,
            "cohorts_measured":     int,
            "best_cohort":          str | None,
        }
    """
    return get_cohort_summary(db, shop)


@router.get("/monthly")
def get_monthly_cohorts_endpoint(
    months: int = 6,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Monthly acquisition cohort analysis.

    Each cohort = customers who made their FIRST order in that month.
    Tracks cumulative revenue, repeat rate, and orders/customer by cohort age.

    Customer identity: uses customer_id (preferred) or customer_email.
    Orders without either are excluded — coverage rate is surfaced honestly.

    Returns:
        {
            "window_months": int,
            "customer_coverage": {
                "total_orders": int,
                "identifiable_orders": int,
                "unidentifiable_orders": int,
                "coverage_rate": float,
            },
            "cohorts": [
                {
                    "cohort_month": str,              # "2026-03"
                    "size": int,                       # unique customers
                    "revenue_total": float,
                    "orders_total": int,
                    "orders_per_customer": float,
                    "revenue_per_customer": float,
                    "repeat_rate": float,              # fraction with 2+ orders
                    "cumulative_revenue": [
                        {"month_age": 0, "revenue": float, "month_revenue": float, "customers_active": int},
                        {"month_age": 1, "revenue": float, "month_revenue": float, "customers_active": int},
                        ...
                    ]
                }
            ],
            "overall": {
                "total_customers": int,
                "repeat_customers": int,
                "repeat_rate": float,
                "avg_orders_per_customer": float,
                "avg_revenue_per_customer": float,
            }
        }
    """
    months = max(1, min(months, 12))
    return get_monthly_cohorts(db, shop, months=months)


@router.get("/ltv")
def get_ltv_summary_endpoint(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
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
    return get_ltv_summary(db, shop)


@router.get("/behavioral")
def get_behavioral_cohorts_endpoint(
    days: int = 90,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Behavioral LTV segmentation — segments customers by pre-purchase behavior.

    Three segmentation dimensions:
      by_engagement: HIGH / MEDIUM / LOW (scroll + dwell + visit frequency)
      by_visit_pattern: REPEAT_VISITOR / SINGLE_VISIT (browsed before purchase)
      by_source: SEARCH / SOCIAL / DIRECT / EMAIL_SMS / REFERRAL / OTHER

    Each segment shows: customers, repeat_rate, avg_revenue, avg_orders.
    Includes AI-generated interpretive insights.

    This endpoint answers:
      "Which behavior patterns create high-LTV customers?"
      "Which acquisition sources bring low-quality buyers?"
      "Should I invest more in retargeting repeat browsers?"
    """
    from app.services.behavioral_cohorts import get_behavioral_cohort_analysis
    days = max(7, min(days, 180))
    return get_behavioral_cohort_analysis(db, shop, days=days)
