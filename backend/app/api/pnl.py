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

from app.core.database import get_db
from app.core.deps import require_merchant_session, require_pro_session
from app.services.pnl_engine import get_pnl_report

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/pnl", tags=["pnl"])

# Lite-accessible P&L sibling router (Strada 2.3, 2026-04-20). Same
# shape + same service; only auth differs. Every major competitor at
# our tier (BeProfit $25+, Lifetimely $35+) ships P&L — it's table-
# stakes for €39, and the backend has always been able to compute it.
lite_router = APIRouter(prefix="/analytics/pnl", tags=["pnl"])




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


class MarginDragProduct(BaseModel):
    product: str
    title: str
    revenue: float
    cogs: float
    cogs_source: str
    margin_eur: float
    margin_pct: float
    units_sold: int


class MarginDragResponse(BaseModel):
    window_days: int
    currency: str
    generated_at: str
    total_revenue: float
    avg_margin_pct: float | None = None
    total_margin_drag_eur: float
    products: list[MarginDragProduct] = []
    methodology: str
    error: str | None = None


@lite_router.get(
    "/margin-drag",
    response_model=MarginDragResponse,
    response_model_exclude_none=False,
)
def get_pnl_margin_drag_lite(
    window_days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=5, ge=1, le=20),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Top-N products dragging total margin down. Strada 4 dominance
    — per-product margin view every competitor at our tier lacks.
    Ranked by lowest margin%, filtered to products with material
    revenue (noise-floored). Drag = how much more monthly margin
    these products would produce if they matched the shop average.
    Not sensitive to tier; opened to Lite per dominate-everywhere
    directive 2026-04-20."""
    from app.services.pnl_engine import get_product_margin_drag
    return get_product_margin_drag(db, shop, window_days=window_days, limit=limit)


@lite_router.get(
    "",
    response_model=PnlReportResponse,
    response_model_exclude_none=False,
)
def get_pnl_lite_endpoint(
    window_days: int = Query(default=30, ge=1, le=90),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Lite-accessible P&L (Strada 2.3). Identical shape + service as
    the Pro /pro/pnl endpoint; only auth differs. Data shown at either
    tier — the positioning locked it to Pro historically, but P&L is
    table-stakes at the €39 band. Opens 2026-04-20 per founder
    directive "strada 2 — completista"."""
    return get_pnl_report(db, shop, window_days=window_days)
