"""
forecasts.py — Probabilistic forecasts endpoint (δ3).

GET /pro/forecast/revenue?horizon_days=14&window_days=60
GET /pro/forecast/churn?horizon_days=30&window_days=90

Both return point estimate + 80/95% prediction intervals + headline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.probabilistic_forecast import forecast_revenue, forecast_churn

router = APIRouter(prefix="/pro/forecast", tags=["forecasts"])




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


@router.get("/churn")
def churn_forecast(
    horizon_days: int = Query(30, ge=1, le=180),
    window_days: int = Query(90, ge=14, le=365),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    return forecast_churn(
        db, shop, horizon_days=horizon_days, window_days=window_days
    )
