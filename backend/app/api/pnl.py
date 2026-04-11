"""
pnl.py — Profit Intelligence endpoint (Pro only).

GET /pro/pnl?window_days=<1-90>

Returns the full P&L waterfall for the shop, computed from real shop_orders
with honest default assumptions for COGS, shipping, and payment fees.

This is the cassettone that closes the single largest competitive gap vs
Lifetimely and Triple Whale: "I don't just show you revenue, I show you what
you keep after costs." The precision field signals how much of the P&L is
real vs estimated so the UI can render honest CTAs to improve precision.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.pnl_engine import get_pnl_report

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/pnl", tags=["pnl"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Response models — emitted into OpenAPI and consumed by PnlReport.tsx.
# Every cost component carries an "estimated" flag so the UI can honestly
# distinguish default-vs-real precision.
# ---------------------------------------------------------------------------


class PnlCogsComponent(BaseModel):
    """Cost of goods sold — default 40% of revenue until merchant inputs real COGS."""
    amount: float
    rate: float
    estimated: bool
    source: str
    note: str


class PnlPaymentFeesComponent(BaseModel):
    """Payment processing fees — Shopify Payments standard (2.9% + €0.30/order)."""
    amount: float
    rate: float
    flat: float
    estimated: bool
    source: str
    note: str


class PnlShippingComponent(BaseModel):
    """Fulfillment/shipping cost — default per-order flat estimate."""
    amount: float
    rate: float
    estimated: bool
    source: str
    note: str


class PnlAdSpendComponent(BaseModel):
    """Advertising spend — not tracked until Meta/Google APIs are wired (Phase 3)."""
    amount: float
    estimated: bool
    source: str
    note: str


class PnlCostBreakdown(BaseModel):
    """Full cost breakdown inside the P&L waterfall."""
    cogs: PnlCogsComponent
    payment_fees: PnlPaymentFeesComponent
    shipping: PnlShippingComponent
    ad_spend: PnlAdSpendComponent


class PnlReportResponse(BaseModel):
    """GET /pro/pnl — Profit Intelligence cassettone source."""
    window_days: int
    currency: str
    precision: str = Field(..., description="'rough' | 'refined' | 'exact'")
    has_data: bool
    order_count: int
    gross_revenue: float
    cogs_coverage_pct: float = Field(
        default=0.0,
        description="Fraction of revenue covered by real per-product COGS (0-100).",
    )
    products_with_cogs: int = Field(
        default=0,
        description="Count of distinct products with a non-NULL cogs_per_unit row.",
    )
    costs: PnlCostBreakdown
    total_costs: float
    gross_profit: float
    net_profit: float
    gross_margin_pct: float
    net_margin_pct: float
    verdict: str
    generated_at: str


@router.get(
    "",
    response_model=PnlReportResponse,
    response_model_exclude_none=False,
)
def get_pnl_endpoint(
    window_days: int = Query(default=30, ge=1, le=90),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Profit Intelligence — deterministic P&L waterfall from real orders.

    Returns gross revenue, cost stack (COGS + fees + shipping + ad spend),
    gross profit, net profit, and margin percentages. Every cost component
    carries an `estimated` flag and a `source` label so the UI can honestly
    distinguish default assumptions from real merchant-provided data.

    Pro-only: require_pro_session enforces plan + session cookie.
    """
    return get_pnl_report(db, shop, window_days=window_days)
