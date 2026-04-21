"""
week_ridge.py — GET /analytics/week-ridge endpoint.

Returns the 7-day Week Ridge chart payload consumed by Lite v5 Zone 4.
Two parallel series (at_risk_eur / captured_eur) in merchant currency,
plus week-over-week captured pct. Cold-start path when a shop has
<3 days of order activity in the last 14 days.

Spec: /docs/LITE_VISUAL_SPEC_v5.md §2 Zone 4 + §9 endpoint contract.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.services.week_ridge import compute_week_ridge

router = APIRouter(tags=["week_ridge"])


class WeekRidgeDay(BaseModel):
    date: str  # ISO YYYY-MM-DD
    at_risk_eur: float
    captured_eur: float


class WeekRidgeResponse(BaseModel):
    shop_domain: str
    days: list[WeekRidgeDay] = Field(default_factory=list)
    # Shop's native currency — all `_eur`-suffixed fields above are
    # in this currency. Historical suffix, covers USD/EUR/GBP/...
    currency: str = "USD"
    week_over_week_captured_pct: float | None = None
    cold_start: bool = False
    generated_at: str | None = None


@router.get(
    "/analytics/week-ridge",
    response_model=WeekRidgeResponse,
    response_model_exclude_none=False,
)
def get_week_ridge(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    7-day Week Ridge payload.

    Data sources (no new tables, read-only):
    - `shop_orders` (captured revenue per day)
    - `events` (abandoned high-intent visitor count per day,
       predicate matches revenue_at_risk.py's recovery model)

    At-risk EUR is an estimate tagged as such in drawer methodology:
    `high_intent_count × 30d_AOV × 0.08 recovery`. Captured EUR is
    real order revenue.

    Cold-start returns `{days: [], cold_start: true}`. The dashboard
    renders "Watching your week build" instead of a chart. No
    fabricated zero-padding.
    """
    return compute_week_ridge(db, shop)
