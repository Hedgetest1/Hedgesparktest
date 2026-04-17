"""
goals.py — CRUD API for merchant goals.

Pro-gated. Redis-backed persistence, no schema migration. Designed to
pair with the Revenue-at-Risk Score (F4) which feeds the merchant the
monetary gap between current run-rate and their declared goal.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api._types import OkResponse
from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["goals"])


class GoalPayload(BaseModel):
    metric: str = Field(..., max_length=64, description="monthly_revenue | monthly_orders | aov | cvr")
    target_value: float = Field(..., gt=0)
    period: str = Field("monthly", max_length=32)
    note: str = Field("", max_length=200)


class GoalResponse(BaseModel):
    metric: str
    target_value: float
    period: str
    set_at: str
    note: str


class GoalProgressResponse(BaseModel):
    metric: str
    target_value: float
    current_value: float
    projected_value: float
    gap_pct: float
    status: str
    narrative: str


class GoalsListResponse(BaseModel):
    shop_domain: str
    goals: list[GoalResponse]


class GoalsProgressResponse(BaseModel):
    shop_domain: str
    progress: list[GoalProgressResponse]
    at_risk_count: int = 0
    off_track_count: int = 0
    # Shop's native currency (USD/EUR/GBP/…) — `target_value`,
    # `current_value`, and `projected_value` for revenue/aov metrics
    # are denominated in this currency.
    currency: str = "USD"


@router.get(
    "/pro/goals",
    response_model=GoalsListResponse,
    response_model_exclude_none=False,
)
def list_goals(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """List all active goals for the merchant."""
    from app.services.goals import get_goals
    goals = get_goals(shop)
    return GoalsListResponse(
        shop_domain=shop,
        goals=[GoalResponse(**g.to_dict()) for g in goals],
    )


@router.post(
    "/pro/goals",
    response_model=GoalResponse,
    response_model_exclude_none=False,
)
def create_or_update_goal(
    payload: GoalPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Create or update a goal. Re-posting the same metric replaces it."""
    from app.services.goals import set_goal
    try:
        g = set_goal(
            shop,
            metric=payload.metric,
            target_value=payload.target_value,
            period=payload.period or "monthly",
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if g is None:
        raise HTTPException(status_code=503, detail="goal storage unavailable")
    return GoalResponse(**g.to_dict())


@router.delete("/pro/goals/{metric}", response_model=OkResponse)
def delete_goal_endpoint(
    metric: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Delete a goal by metric name."""
    from app.services.goals import delete_goal
    removed = delete_goal(shop, metric)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no goal for metric {metric!r}")
    return {"deleted": True, "metric": metric}


@router.get(
    "/pro/goals/progress",
    response_model=GoalsProgressResponse,
    response_model_exclude_none=False,
)
def get_progress(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Return live progress for every active goal, including projected
    end-of-month values and at-risk classification.
    """
    from app.services.goals import compute_goal_progress
    from app.services.revenue_metrics import get_shop_currency
    progress = compute_goal_progress(db, shop)
    at_risk = sum(1 for p in progress if p.status == "at_risk")
    off_track = sum(1 for p in progress if p.status == "off_track")
    return GoalsProgressResponse(
        shop_domain=shop,
        progress=[GoalProgressResponse(**p.to_dict()) for p in progress],
        at_risk_count=at_risk,
        off_track_count=off_track,
        currency=get_shop_currency(db, shop) or "USD",
    )
