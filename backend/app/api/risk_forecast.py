"""
risk_forecast.py — GET /pro/risk-forecast API endpoint.

Forward-looking companion to /pro/revenue-at-risk. Returns a 7-day
projection of the Revenue-at-Risk Score computed from the rolling
history we accumulate each time RARS is computed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import require_pro_session

router = APIRouter(tags=["risk_forecast"])


@router.get("/pro/risk-forecast")
def get_forecast(shop: str = Depends(require_pro_session)):
    """
    7-day forecast of Revenue-at-Risk, with direction (rising/falling/
    stable), confidence level, and the underlying history for a small
    dashboard sparkline.
    """
    from app.services.risk_forecast import get_risk_forecast
    return get_risk_forecast(shop)
