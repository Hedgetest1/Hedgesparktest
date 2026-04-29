"""
causal_lift.py — GET /pro/causal-lift API endpoint.

Returns causal revenue attribution using RCT holdout measurement.
THE competitive claim: "our nudges CAUSED +X% conversion, proven
with holdout groups at Y% statistical confidence."

Pro-gated.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_scale_session

router = APIRouter(tags=["causal_lift"])


class CausalLiftResponse(BaseModel):
    shop_domain: str
    total_lift_pct: float
    attributed_revenue_eur: float
    confidence: float
    nudges_measured: int
    exposed_visitors: int | None = None
    holdout_visitors: int | None = None
    methodology: str
    detail: str
    # Shop's native currency — attributed_revenue_eur is in this currency.
    currency: str = "USD"


class RecommendationImpactRow(BaseModel):
    action_type: str
    action_date: str
    pre_revenue: float
    post_revenue: float
    impact_pct: float


class RecommendationImpactResponse(BaseModel):
    shop_domain: str
    actions_measured: int
    avg_impact_pct: float
    impacts: list[RecommendationImpactRow] = Field(default_factory=list)
    methodology: str
    detail: str


@router.get("/pro/causal-lift", response_model=CausalLiftResponse)
def get_causal_lift(
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """
    Causal revenue attribution via RCT holdout measurement.
    Shows proven lift from nudges with statistical confidence.
    """
    from app.services.causal_intervention_engine import measure_nudge_lift
    return measure_nudge_lift(db, shop)


@router.get("/pro/recommendation-impact", response_model=RecommendationImpactResponse)
def get_recommendation_impact(
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """
    Quasi-experimental measurement of recommendation impact.
    Pre/post revenue comparison for acted-upon recommendations.
    """
    from app.services.causal_intervention_engine import measure_recommendation_impact
    return measure_recommendation_impact(db, shop)
