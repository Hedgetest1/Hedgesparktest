"""
instant_intelligence.py — Endpoint for 60s aha-moment onboarding.

GET /pro/instant-intelligence
    Returns the Shopify-orders-backfill snapshot: AOV, monthly revenue,
    top products, preview RARS, narrative. Used by the dashboard
    immediately after install to show merchants a preview of intelligence
    before event collection has produced any data of its own.

If the cache is empty (merchant just installed), this endpoint triggers
the backfill inline and returns status='computing' so the frontend can
poll with a 3-5s delay.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_merchant_session
from app.services.instant_onboarding import compute_instant_intelligence, trigger_instant_intelligence_async

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro", tags=["instant_intelligence"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TopProduct(BaseModel):
    id: str
    title: str
    revenue: float
    units: int


class InstantIntelligenceResponse(BaseModel):
    shop_domain: str
    status: str  # 'ready' | 'empty' | 'computing'
    reason: str | None = None
    message: str | None = None
    computed_at: str | None = None
    backfill_days: int | None = None
    currency: str | None = None
    order_count_90d: int | None = None
    total_revenue_90d: float | None = None
    aov: float | None = None
    monthly_revenue_estimate: float | None = None
    refund_rate_pct: float | None = None
    top_products: list[TopProduct] | None = None
    preview_rars_monthly: float | None = None
    narrative: str | None = None


@router.get("/instant-intelligence", response_model=InstantIntelligenceResponse)
def get_instant_intelligence(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    # Try cache first
    try:
        from app.core.redis_client import _client
        from app.services.instant_onboarding import _redis_key
        import json as _json

        rc = _client()
        if rc is not None:
            cached = rc.get(_redis_key(shop))
            if cached:
                data = _json.loads(cached)
                return InstantIntelligenceResponse(**data)
    except Exception as exc:
        log.warning("instant_intelligence: cache read failed: %s", exc)

    # Cache miss — trigger async backfill and return 'computing'
    trigger_instant_intelligence_async(shop)
    return InstantIntelligenceResponse(
        shop_domain=shop,
        status="computing",
        message="HedgeSpark is reading your last 90 days of orders. Refresh in a few seconds.",
    )


@router.post("/instant-intelligence/refresh", response_model=InstantIntelligenceResponse)
def refresh_instant_intelligence(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """Force-recompute bypassing the cache."""
    data = compute_instant_intelligence(db, shop)
    return InstantIntelligenceResponse(**data)
