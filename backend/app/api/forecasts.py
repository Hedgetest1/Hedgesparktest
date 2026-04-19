"""
forecasts.py — Probabilistic forecasts endpoint (δ3).

GET /pro/forecast/revenue?horizon_days=14&window_days=60
GET /pro/forecast/churn?horizon_days=30&window_days=90

Both return point estimate + 80/95% prediction intervals + headline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.probabilistic_forecast import forecast_revenue, forecast_churn

router = APIRouter(prefix="/pro/forecast", tags=["forecasts"])


class ChurnForecastResponse(BaseModel):
    """Strongly-typed shape for the churn-forecast endpoint.

    The service's happy and insufficient-data paths do not include all
    fields uniformly; fields with defaults here absorb that variance so
    the frontend can read any attribute without optional-chaining every
    access.
    """
    shop_domain: str
    method: str = "holt_double_exp"
    metric: str = "daily_newly_silent_customers"
    horizon_days: int
    window_days: int
    dates: list[str] = Field(default_factory=list)
    observed_values: list[float] = Field(default_factory=list)
    fitted_values: list[float] = Field(default_factory=list)
    forecast_values: list[float] = Field(default_factory=list)
    forecast_point: float = 0.0
    forecast_lower_80: float = 0.0
    forecast_upper_80: float = 0.0
    forecast_lower_95: float = 0.0
    forecast_upper_95: float = 0.0
    residual_std: float = 0.0
    r_squared: float = 0.0
    direction: str = "stable"
    confidence: str = "insufficient"
    headline: str = ""
    total_projected_churn: int = 0
    generated_at: str | None = None
    status: str | None = None
    reason: str | None = None


@router.get("/revenue")
def revenue_forecast(
    horizon_days: int = Query(14, ge=1, le=60),
    window_days: int = Query(60, ge=7, le=365),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    return forecast_revenue(
        db, shop, horizon_days=horizon_days, window_days=window_days
    )


@router.get("/churn", response_model=ChurnForecastResponse)
def churn_forecast(
    horizon_days: int = Query(30, ge=1, le=180),
    window_days: int = Query(90, ge=14, le=365),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    return forecast_churn(
        db, shop, horizon_days=horizon_days, window_days=window_days
    )
