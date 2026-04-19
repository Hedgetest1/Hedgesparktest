"""
risk_forecast.py — GET /pro/risk-forecast API endpoint.

Forward-looking companion to /pro/revenue-at-risk. Returns a 7-day
projection of the Revenue-at-Risk Score computed from the rolling
history we accumulate each time RARS is computed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.deps import require_pro_session

router = APIRouter(tags=["risk_forecast"])


class RiskHistoryPoint(BaseModel):
    """One observation in the rolling RARS history."""
    ts: str
    total_at_risk_eur: float


class RiskForecastResponse(BaseModel):
    shop_domain: str
    status: str
    today_value_eur: float | None = None
    forecast_7d_eur: float | None = None
    forecast_7d_lower_80_eur: float | None = None
    forecast_7d_upper_80_eur: float | None = None
    forecast_7d_lower_95_eur: float | None = None
    forecast_7d_upper_95_eur: float | None = None
    residual_std_error: float | None = None
    week_delta_eur: float | None = None
    week_delta_pct: float | None = None
    direction: str | None = None
    confidence: str | None = None
    r_squared: float | None = None
    points_used: int | None = None
    slope_per_day: float | None = None
    headline: str | None = None
    history: list[RiskHistoryPoint] = Field(default_factory=list)
    detail: str | None = None


@router.get("/pro/risk-forecast", response_model=RiskForecastResponse)
def get_forecast(shop: str = Depends(require_pro_session)):
    """
    7-day forecast of Revenue-at-Risk, with direction (rising/falling/
    stable), confidence level, and the underlying history for a small
    dashboard sparkline.
    """
    from app.services.risk_forecast import get_risk_forecast
    return get_risk_forecast(shop)
