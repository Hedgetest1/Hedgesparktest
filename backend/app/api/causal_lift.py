"""
causal_lift.py — GET /pro/causal-lift API endpoint.

Returns causal revenue attribution using RCT holdout measurement.
THE competitive claim: "our nudges CAUSED +X% conversion, proven
with holdout groups at Y% statistical confidence."

Pro-gated.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["causal_lift"])


@router.get("/pro/causal-lift")
def get_causal_lift(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Causal revenue attribution via RCT holdout measurement.
    Shows proven lift from nudges with statistical confidence.
    """
    from app.services.causal_intervention_engine import measure_nudge_lift
    return measure_nudge_lift(db, shop)


@router.get("/pro/recommendation-impact")
def get_recommendation_impact(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Quasi-experimental measurement of recommendation impact.
    Pre/post revenue comparison for acted-upon recommendations.
    """
    from app.services.causal_intervention_engine import measure_recommendation_impact
    return measure_recommendation_impact(db, shop)
