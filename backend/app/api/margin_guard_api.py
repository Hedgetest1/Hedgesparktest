"""
margin_guard_api.py — Expose margin snapshot + what-if endpoint.

GET /pro/margin/snapshot
    Current margin + precision + floor config.

GET /pro/margin/check?discount_pct=-10
    What-if: would this discount be allowed?

Merchant-facing dashboard card uses /snapshot; trust-contract grant
modal uses /check for live validation as the merchant drags sliders.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.margin_guard import get_margin_snapshot, check_discount_safe

router = APIRouter(prefix="/pro/margin", tags=["margin_guard"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class MarginSnapshot(BaseModel):
    shop_domain: str
    window_days: int
    revenue_eur: float
    cogs_eur: float
    gross_margin_eur: float
    gross_margin_pct: float
    cogs_pct_used: float
    precision: str
    min_required_margin_pct: float
    computed_at: str


class MarginCheckResponse(BaseModel):
    allowed: bool
    reason: str
    current_margin_pct: float
    projected_margin_pct: float
    min_required_pct: float
    precision: str
    total_revenue_30d: float
    total_cogs_30d: float


@router.get("/snapshot", response_model=MarginSnapshot)
def margin_snapshot(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    return MarginSnapshot(**get_margin_snapshot(db, shop))


@router.get("/check", response_model=MarginCheckResponse)
def margin_check(
    discount_pct: float = Query(..., ge=-100.0, le=100.0),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    r = check_discount_safe(db, shop, discount_pct)
    return MarginCheckResponse(
        allowed=r.allowed,
        reason=r.reason,
        current_margin_pct=r.current_margin_pct,
        projected_margin_pct=r.projected_margin_pct,
        min_required_pct=r.min_required_pct,
        precision=r.precision,
        total_revenue_30d=r.total_revenue_30d,
        total_cogs_30d=r.total_cogs_30d,
    )
