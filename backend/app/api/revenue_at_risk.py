"""
revenue_at_risk.py — GET /pro/revenue-at-risk API endpoint.

Returns the Revenue-at-Risk Score (RARS) — the hero number of the
HedgeSpark dashboard. Pro-gated, cached 5 min.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["revenue_at_risk"])


class RARSComponentResponse(BaseModel):
    source: str
    loss_eur: float
    narrative: str
    evidence: dict = Field(default_factory=dict)


class RARSResponse(BaseModel):
    shop_domain: str
    total_at_risk_eur: float = 0.0
    prevented_eur_this_month: float = 0.0
    net_roi_eur: float = 0.0
    components: list[RARSComponentResponse] = Field(default_factory=list)
    generated_at: str | None = None
    headline: str | None = None


@router.get(
    "/pro/revenue-at-risk",
    response_model=RARSResponse,
    response_model_exclude_none=False,
)
def get_rars(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Return the Revenue-at-Risk Score: one number showing how much
    monthly revenue is currently at risk, broken down by source so
    merchants can drill into any component and see the action plan.

    This is the hero metric of the HedgeSpark dashboard. Every other
    feature (benchmarks, refund loss, goals, segments) feeds into or
    drills down from this number.
    """
    from app.services.revenue_at_risk import get_revenue_at_risk
    result = get_revenue_at_risk(db, shop)
    # Strip internal debug field
    result.pop("_prevent_evidence", None)
    return result
