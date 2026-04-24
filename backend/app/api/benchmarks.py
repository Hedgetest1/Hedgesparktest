"""
benchmarks.py — GET /pro/benchmarks API endpoint.

Returns the industry benchmark report for the authenticated shop.
Pro-gated. Built on app.services.benchmarks. No side effects, cached 6h.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["benchmarks"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class BenchmarkMetric(BaseModel):
    value: float
    band: str
    peer_count: int
    percentile_rank: float = Field(..., description="0-100, higher = better")
    p25: float
    p50: float
    p75: float
    p90: float
    recovery_to_p75_eur: float = Field(
        ..., description="Monthly € you would recover by moving to p75 peers"
    )
    status: str = Field(
        ..., description="top_decile | top_quartile | above_median | below_median"
    )
    narrative: str


class ProductConcentration(BaseModel):
    """Pareto-style concentration: how many products drive 80% of
    revenue. High concentration → narrow catalog risk; low → healthy
    diversification. Surfaced alongside the standard benchmark metrics
    as a complementary moat signal."""
    total_products: int
    products_for_80pct_revenue: int
    concentration_ratio: float
    narrative: str


class BenchmarkResponse(BaseModel):
    shop_domain: str
    band: str | None = None
    peer_count: int = 0
    metrics: dict[str, BenchmarkMetric] = Field(default_factory=dict)
    total_recovery_potential_eur: float = 0.0
    # Shop's native currency — dashboard renders recovery potential +
    # per-metric money values with the matching symbol (the `_eur`
    # suffix is historical; the underlying value is native).
    currency: str = "USD"
    generated_at: str | None = None
    note: str | None = None
    error: str | None = None
    # Strada 4 extension — get_extended_benchmark_report surfaces the
    # merchant's product concentration (80/20) so the card can show
    # "X products drive 80% of your revenue" alongside peer metrics.
    product_concentration: ProductConcentration | None = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/pro/benchmarks",
    response_model=BenchmarkResponse,
    response_model_exclude_none=False,
)  # ui-exempt: deprecated
def get_benchmarks(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Return the merchant's benchmark report vs peers in the same revenue
    band. The response is loss-framed: every metric carries a
    `recovery_to_p75_eur` showing how much monthly revenue could be
    recovered by moving from the current position to the 75th percentile
    of peers.

    Privacy: minimum 10 peers per band. Below that threshold the response
    contains a `note` field and no metric comparisons.
    """
    from app.services.benchmarks import get_extended_benchmark_report
    return get_extended_benchmark_report(db, shop)


@router.get(
    "/analytics/benchmarks",
    response_model=BenchmarkResponse,
    response_model_exclude_none=False,
)
def get_benchmarks_lite_accessible(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Lite-accessible benchmarks endpoint.

    Returns the same BenchmarkResponse shape as /pro/benchmarks — no
    data-sensitivity difference between tiers for this surface. The
    Pro/Lite split was historically a positioning choice; per founder
    directive 2026-04-20 ("strada 2 — completista"), peer benchmarks
    become part of the €39 Lite value prop because every competitor at
    the tier already shows some form of peer comparison.

    Same privacy gate (N≥10 peers per band), same loss framing, same
    6-hour Redis cache — the only difference is the session dependency.

    Strada 4 (dominate): upgraded to get_extended_benchmark_report
    which returns the base 4 metrics PLUS CVR percentile and product
    concentration (80/20 rule — how many products drive 80% of
    revenue). This puts Lite above Varos on metric breadth.
    """
    from app.services.benchmarks import get_extended_benchmark_report
    return get_extended_benchmark_report(db, shop)
