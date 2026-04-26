"""
benchmarks_vertical.py — Phase Ω vertical-aware benchmark API.

  GET /pro/benchmarks/vertical    — merchant report
  GET /pro/vertical                — current classification
  GET /ops/benchmarks/pool         — operator-only moat depth view
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["benchmarks_vertical"])


class VerticalSelfResponse(BaseModel):
    shop_domain: str
    vertical: str
    confidence: float
    runner_up: str | None = None
    runner_up_confidence: float = 0.0
    sample_size: int
    classified_at: str


class VerticalBenchmarkResponse(BaseModel):
    shop_domain: str
    vertical: str | None = None
    vertical_display: str | None = None
    band: str | None = None
    peer_count: int | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    total_recovery_potential_eur: float | None = None
    generated_at: str | None = None
    scope: str | None = None
    note: str | None = None
    fallback_baselines: dict[str, Any] | None = None
    error: str | None = None


@router.get("/pro/benchmarks/vertical", response_model=VerticalBenchmarkResponse)
def get_vertical_benchmarks(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Vertical-aware benchmark report (Phase Ω moat)."""
    from app.services.benchmarks_vertical import get_vertical_benchmark_report
    return get_vertical_benchmark_report(db, shop)


@router.get(
    "/analytics/benchmarks/vertical",
    response_model=VerticalBenchmarkResponse,
)
def get_vertical_benchmarks_lite(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Lite-accessible vertical benchmarks (Strada 3.1, 2026-04-20).
    Same shape + service as the Pro sibling; auth differs. Vertical-
    aware peer comparison was historically Pro-gated but closes the
    Varos-vertical gap at the €39 tier per founder directive."""
    from app.services.benchmarks_vertical import get_vertical_benchmark_report
    return get_vertical_benchmark_report(db, shop)


def _build_vertical_response(db: Session, shop: str) -> VerticalSelfResponse:
    from app.services.vertical_classifier import classify_shop
    c = classify_shop(db, shop)
    return VerticalSelfResponse(
        shop_domain=c.shop_domain,
        vertical=c.vertical,
        confidence=c.confidence,
        runner_up=c.runner_up,
        runner_up_confidence=c.runner_up_confidence,
        sample_size=c.sample_size,
        classified_at=c.classified_at,
    )


@router.get("/pro/vertical", response_model=VerticalSelfResponse)
def get_my_vertical(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Return the merchant's classified vertical + confidence (Pro)."""
    return _build_vertical_response(db, shop)


@router.get("/analytics/vertical", response_model=VerticalSelfResponse)
def get_my_vertical_lite(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Lite-accessible classification — sibling of `/pro/vertical`.

    Tier-gate fix 2026-04-26: VerticalBenchmarksCard renders on the
    Lite floor (commit 2af2cd6 opened the benchmark endpoint to Lite
    via `/analytics/benchmarks/vertical`) but the classification
    fetch stayed on `/pro/vertical`. Lite-tier merchants got 403,
    surfaced by `verify_lite_dashboard_e2e.js` axis-2 test cell.
    Same handler as the Pro sibling — classification is per-merchant
    metadata, not competitive intel."""
    return _build_vertical_response(db, shop)


@router.get("/ops/benchmarks/pool")
def get_pool_stats(
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None),
):
    """
    Operator endpoint: shows how deep the network-effect moat is.
    Each (vertical, band) bucket above k=8 is a defendable comparison
    that competitors with smaller pools cannot reproduce.
    """
    import os
    expected = os.getenv("OPS_API_KEY", "")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=403, detail="forbidden")
    from app.services.benchmarks_vertical import get_vertical_pool_stats
    return get_vertical_pool_stats(db)
