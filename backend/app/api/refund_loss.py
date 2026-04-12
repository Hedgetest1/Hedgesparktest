"""
refund_loss.py — GET /pro/refund-losses API endpoint.

Returns the loss-framed product decline report for the authenticated shop.
Pro-gated. Cached 3h via app.services.refund_loss.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["refund_loss"])


class ProductLossRow(BaseModel):
    product_title: str
    product_id: str | None = None
    orders_recent_14d: int
    orders_prior_14d: int
    avg_price_recent: float
    avg_price_prior: float
    revenue_recent_14d: float
    revenue_prior_14d: float
    loss_eur: float = Field(..., description="Monthly-normalized € loss")
    decline_pct: float
    reason: str


class RefundLossResponse(BaseModel):
    shop_domain: str
    total_loss_eur_per_month: float = 0.0
    product_count: int = 0
    products: list[ProductLossRow] = Field(default_factory=list)
    generated_at: str | None = None
    method: str | None = None
    headline: str | None = None
    note: str | None = None
    error: str | None = None


@router.get(
    "/pro/refund-losses",
    response_model=RefundLossResponse,
    response_model_exclude_none=False,
)
def get_refund_losses(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Return the top-10 products with declining order momentum as a
    loss-framed report. Each row carries a monthly-normalized `loss_eur`
    you would recover by stopping the decline.

    v1 uses order-frequency decline as a proxy for refund/return impact.
    v2 (memory: F2 note) will switch to direct Shopify refund webhook
    ingestion without changing this API.
    """
    from app.services.refund_loss import get_refund_loss_report
    return get_refund_loss_report(db, shop)
