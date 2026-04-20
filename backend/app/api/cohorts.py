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
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session
from app.services.cohort_engine import get_cohort_retention, get_cohort_summary
from app.services.ltv_engine import (
    get_monthly_cohorts,
    get_ltv_summary,
    get_product_ltv_contribution,
    get_predicted_ltv,
)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------
# These exist so FastAPI can emit a full OpenAPI response schema, which
# `openapi-typescript` then picks up and turns into compile-time types on the
# frontend. Without response_model, the frontend sees `unknown` for the body
# and cannot type-check field access.
#
# Keep these in sync with the dicts returned by ltv_engine.py — if a field is
# added to the engine output, add it here too. A mismatch is caught by
# FastAPI's response validation at runtime (which is a bit late, but better
# than the old silent drift).
# ---------------------------------------------------------------------------


class GatewayProductRow(BaseModel):
    """One row in the Gateway Products cassettone."""
    product: str = Field(..., description="Product key (slug or URL)")
    title: str | None = None
    buyer_count: int
    avg_buyer_ltv: float
    avg_buyer_orders: float
    buyer_repeat_rate: float = Field(..., ge=0.0, le=1.0)
    gateway_rate: float = Field(..., ge=0.0, le=1.0)
    is_gateway: bool


class GatewayProductsResponse(BaseModel):
    """GET /pro/cohorts/ltv/products response shape."""
    shop_domain: str
    products: list[GatewayProductRow]


class PredictedLtvCustomer(BaseModel):
    """One customer in the Predicted LTV ranking."""
    customer_key: str
    email_hint: str | None = None
    total_orders: int
    total_spend: float
    aov: float
    days_since_last: float
    repeat_probability_30d: float = Field(..., ge=0.0, le=1.0)
    predicted_30d_value: float
    predicted_12m_ltv: float


class PredictedLtvResponse(BaseModel):
    """GET /pro/cohorts/ltv/customers response shape."""
    shop_domain: str
    customers: list[PredictedLtvCustomer]
    count: int


# ---- Weekly cohort retention ---------------------------------------------


class WeeklyCohortRow(BaseModel):
    """One weekly cohort row with dynamic retention keys."""
    cohort_week: str
    cohort_start: str
    size: int
    revenue_total: float
    # Dynamic keys like "week_1", "week_2", ... Values are retention rates 0..1.
    retention: dict[str, float]


class WeeklyCohortsResponse(BaseModel):
    """GET /pro/cohorts (weekly retention matrix)."""
    window_weeks: int
    generated_at: str
    cohorts: list[WeeklyCohortRow]
    avg_week_1_retention: float
    avg_week_4_retention: float
    best_cohort: str | None = None
    total_customers: int


class CohortSummaryResponse(BaseModel):
    """GET /pro/cohorts/summary — high-level retention stats."""
    avg_week_1_retention: float
    avg_week_4_retention: float
    total_customers: int
    cohorts_measured: int
    best_cohort: str | None = None


# ---- Monthly cohort / LTV ------------------------------------------------


class CustomerCoverageBlock(BaseModel):
    """Customer identifiability coverage for the monthly cohort window."""
    total_orders: int
    identifiable_orders: int
    unidentifiable_orders: int
    coverage_rate: float


class CumulativeRevenuePoint(BaseModel):
    """One point on a cohort's cumulative revenue curve by month age."""
    month_age: int
    revenue: float
    month_revenue: float
    customers_active: int


class MonthlyCohortRow(BaseModel):
    """One monthly acquisition cohort row."""
    cohort_month: str
    size: int
    revenue_total: float
    orders_total: int
    orders_per_customer: float
    revenue_per_customer: float
    repeat_rate: float
    cumulative_revenue: list[CumulativeRevenuePoint]


class MonthlyCohortsOverall(BaseModel):
    """Overall lifetime metrics across all monthly cohorts in the window."""
    total_customers: int
    repeat_customers: int
    repeat_rate: float
    avg_orders_per_customer: float
    avg_revenue_per_customer: float


class MonthlyCohortsResponse(BaseModel):
    """GET /pro/cohorts/monthly — Customer Economics cassettone source."""
    window_months: int
    generated_at: str
    customer_coverage: CustomerCoverageBlock
    cohorts: list[MonthlyCohortRow]
    overall: MonthlyCohortsOverall


class LtvSummaryResponse(BaseModel):
    """GET /pro/cohorts/ltv — high-level LTV summary."""
    total_customers: int
    repeat_rate: float
    avg_orders_per_customer: float
    avg_revenue_per_customer: float
    top_cohort_month: str | None = None
    customer_coverage_rate: float


# ---- Behavioral LTV segmentation -----------------------------------------


class BehavioralSegmentRow(BaseModel):
    """One row inside a behavioral segmentation dimension."""
    segment: str
    customers: int
    repeat_rate: float
    avg_revenue: float
    avg_orders: float
    total_revenue: float = 0.0


class BehavioralDataCoverage(BaseModel):
    """Identifiability coverage for the behavioral cohort window."""
    total_customers: int
    segmentable_customers: int
    coverage_rate: float


class BehavioralSegmentsBlock(BaseModel):
    """Three segmentation dimensions: engagement, visit pattern, source."""
    by_engagement: list[BehavioralSegmentRow]
    by_visit_pattern: list[BehavioralSegmentRow]
    by_source: list[BehavioralSegmentRow]


class BehavioralCohortsResponse(BaseModel):
    """GET /pro/cohorts/behavioral — Behavioral DNA cassettone source."""
    window_days: int
    generated_at: str
    data_coverage: BehavioralDataCoverage
    segments: BehavioralSegmentsBlock
    insights: list[str]


log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/cohorts", tags=["cohorts"])

# Lite-accessible sibling router for the subset of cohort endpoints
# that are part of the €39 tier (Strada 2, 2026-04-20). Only the
# top-level retention summary is exposed — the full matrix, monthly
# cohorts, per-customer LTV, and behavioral cohorts remain Pro-gated
# because their depth + volume is the Pro moat. Lite gets the "how
# are we doing overall at retaining customers?" glance.
lite_router = APIRouter(prefix="/analytics/cohorts", tags=["cohorts"])


@lite_router.get(
    "/summary",
    response_model=CohortSummaryResponse,
    response_model_exclude_none=False,
)
def get_cohort_summary_lite(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Lite-accessible retention summary. Same shape + service as the
    Pro-gated /pro/cohorts/summary endpoint; only the auth dependency
    differs. Data is not sensitive across tiers — the split was a
    positioning choice we relaxed per founder directive 2026-04-20."""
    return get_cohort_summary(db, shop)


@lite_router.get(
    "/monthly",
    response_model=MonthlyCohortsResponse,
    response_model_exclude_none=False,
)
def get_monthly_cohorts_lite(
    months: int = 6,
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Lite-accessible monthly cohort analysis (Strada 3.3, 2026-04-20).
    Same service + response shape as /pro/cohorts/monthly. Each monthly
    acquisition cohort with cumulative revenue, orders/customer,
    revenue/customer, repeat rate. The per-customer LTV drill-down
    stays Pro (depth-moat)."""
    return get_monthly_cohorts(db, shop, months=months)




@router.get(
    "",
    response_model=WeeklyCohortsResponse,
    response_model_exclude_none=False,
)
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


@router.get(
    "/summary",
    response_model=CohortSummaryResponse,
    response_model_exclude_none=False,
)
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


@router.get(
    "/monthly",
    response_model=MonthlyCohortsResponse,
    response_model_exclude_none=False,
)
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


@router.get(
    "/ltv",
    response_model=LtvSummaryResponse,
    response_model_exclude_none=False,
)
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


@router.get(
    "/ltv/products",
    response_model=GatewayProductsResponse,
    response_model_exclude_none=False,
)
def get_product_ltv_endpoint(
    limit: int = 20,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Product LTV contribution — which products drive high-LTV customers.

    Returns per-product: avg buyer LTV, repeat rate, gateway vs repeat flag.
    Gateway products = bought as first order > 50% of the time.
    """
    return get_product_ltv_contribution(db, shop, limit=min(limit, 50))


@router.get(
    "/ltv/customers",
    response_model=PredictedLtvResponse,
    response_model_exclude_none=False,
)
def get_predicted_ltv_endpoint(
    limit: int = 50,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Top customers with predicted LTV.

    Returns ranked list with: total spend, AOV, repeat probability (30d),
    predicted 30-day value, predicted 12-month LTV.
    """
    return get_predicted_ltv(db, shop, limit=min(limit, 100))


@router.get(
    "/behavioral",
    response_model=BehavioralCohortsResponse,
    response_model_exclude_none=False,
)
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
