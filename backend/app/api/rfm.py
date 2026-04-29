"""
rfm.py — RFM customer segmentation API.

G2 Lite parity gap close (2026-04-29). Putler $20, Glew (free), Mipler
all ship 11-named-segment RFM at entry tier — €39 Lite matches.

Endpoint: GET /analytics/rfm/segments
Lite-accessible (require_merchant_session). Cached 5min per shop.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_read_db
from app.core.deps import require_merchant_session

router = APIRouter(tags=["rfm"])


class RfmSampleCustomer(BaseModel):
    id: str
    orders: int
    revenue: float
    last_order_days_ago: int


class RfmSegment(BaseModel):
    name: str
    count: int
    revenue: float
    share_pct: float
    # `description` not `copy` — Pydantic v2 emits a UserWarning when a
    # field shadows BaseModel.copy. Same merchant-facing string either way.
    description: str = ""
    sample_customers: list[RfmSampleCustomer] = Field(default_factory=list)


class RfmSegmentsResponse(BaseModel):
    shop_domain: str
    currency: str
    total_customers: int
    generated_at: str
    segments: list[RfmSegment] = Field(default_factory=list)


@router.get("/analytics/rfm/segments", response_model=RfmSegmentsResponse)
def get_rfm_segments(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
) -> dict[str, Any]:
    """Return the shop's customer base segmented into 11 named RFM
    cells (Champions / Loyal / At Risk / Lost / etc.). Quintile-based
    on the shop's own data — no global thresholds, so a small store
    and a large store both produce a usable segmentation.

    The `sample_customers` list per segment uses non-PII hashed IDs
    (cust_<8hex>) so the merchant can drill in without HedgeSpark
    exposing emails. Recency in days; revenue in shop's primary
    currency."""
    from app.services.rfm import compute_rfm_segments
    return compute_rfm_segments(db, shop)
