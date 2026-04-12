"""segment_compare.py — GET /pro/segments/compare API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["segment_compare"])


class SegmentSnapshotResponse(BaseModel):
    product_url: str
    hot_visitors: int
    warm_visitors: int
    cold_visitors: int
    hot_cvr_estimate: float | None = None
    estimated_revenue_window: float
    total_active: int


class CompareDeltaResponse(BaseModel):
    hot_visitors_delta: int
    revenue_delta_eur: float
    winner: str
    loss_gap_eur: float
    narrative: str


class SegmentCompareResponse(BaseModel):
    shop_domain: str
    window_hours: int
    product_a: SegmentSnapshotResponse
    product_b: SegmentSnapshotResponse
    delta: CompareDeltaResponse
    generated_at: str


@router.get(
    "/pro/segments/compare",
    response_model=SegmentCompareResponse,
    response_model_exclude_none=False,
)
def compare_segments(
    product_a: str = Query(..., description="First product URL (canonical /products/handle)"),
    product_b: str = Query(..., description="Second product URL"),
    hours: int = Query(default=72, ge=1, le=168),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Side-by-side audience segment comparison between two products.
    Loss-framed: the loser's gap in € is quantified and the winner is
    explicitly named.
    """
    from app.services.segment_compare import compare_two_products
    return compare_two_products(db, shop, product_a, product_b, hours=hours)
