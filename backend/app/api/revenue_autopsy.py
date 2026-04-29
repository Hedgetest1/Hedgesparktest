"""
revenue_autopsy.py — GET /pro/revenue-autopsy API endpoint.

Returns the product-level "why did revenue change" analysis.
Pro-gated. Cached 3h via app.services.revenue_autopsy.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_scale_session

router = APIRouter(tags=["revenue_autopsy"])


class RevenueAutopsySummary(BaseModel):
    declining_count: int
    growing_count: int
    total_loss_per_week: float
    total_gain_per_week: float
    top_decline_cause: str | None = None


class RevenueAutopsyResponse(BaseModel):
    shop_domain: str
    products: list[dict[str, Any]] = Field(default_factory=list)
    summary: RevenueAutopsySummary
    headline: str
    # Shop's native currency — all money fields in products/summary
    # (revenue_delta_eur, total_loss_per_week, impact_eur, aov_*) are
    # in this currency.
    currency: str = "USD"
    generated_at: str


@router.get("/pro/revenue-autopsy", response_model=RevenueAutopsyResponse)
def get_revenue_autopsy(
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """
    Product-level revenue change decomposition:
    traffic delta + conversion delta + value delta per product.
    """
    from app.services.revenue_autopsy import compute_product_autopsy
    return compute_product_autopsy(db, shop)
