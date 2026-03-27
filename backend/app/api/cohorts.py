"""
cohorts.py — Cohort retention analytics (Pro only).

GET /pro/cohorts?shop=&weeks=
    Full weekly cohort retention matrix.

GET /pro/cohorts/summary?shop=
    High-level retention stats for the dashboard banner.

This is the feature that directly attacks Lifetimely / Peel on their core
territory.  The key advantage WishSpark will eventually have:

    "Cohorts with high behavioral engagement in week 0 have 2x retention
    at week 8 compared to low-engagement cohorts."

Lifetimely structurally cannot show that because they have no behavioral data.
We do.  This is the foundation for that future positioning.

For now: honest weekly cohort retention from real shop_orders data.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.cohort_engine import get_cohort_retention, get_cohort_summary

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
