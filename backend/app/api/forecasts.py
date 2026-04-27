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
from app.core.deps import require_merchant_session, require_pro_session
from app.services.probabilistic_forecast import forecast_revenue, forecast_churn

router = APIRouter(prefix="/pro/forecast", tags=["forecasts"])

# Lite-accessible sibling router. Per parity doctrine 2026-04-27:
# every $0-60 competitor feature → we build. Lebesgue $59 + Forthcast
# $19.99 ship per-product forecasts at entry tier — we match.
lite_router = APIRouter(prefix="/analytics/forecast", tags=["forecasts"])


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


# ---------------------------------------------------------------------------
# Per-SKU forecast — Gap #6 close (brutal $0-70 audit + parity doctrine)
# ---------------------------------------------------------------------------

class SkuForecastRow(BaseModel):
    product_key: str
    title: str
    observed_revenue: float
    forecast_point: float
    forecast_lower_80: float
    forecast_upper_80: float
    forecast_lower_95: float
    forecast_upper_95: float
    delta_pct: float
    direction: str
    confidence: str
    accuracy_pct: float | None = None
    n_days: int
    r2: float


class SkuForecastBigMover(BaseModel):
    product_key: str
    title: str
    delta_pct: float


class SkuForecastResponse(BaseModel):
    shop_domain: str
    horizon_days: int
    window_days: int
    currency: str
    generated_at: str
    products: list[SkuForecastRow] = []
    biggest_riser: SkuForecastBigMover | None = None
    biggest_faller: SkuForecastBigMover | None = None
    insight: str


@lite_router.get(
    "/by-sku",
    response_model=SkuForecastResponse,
    response_model_exclude_none=False,
)
def get_forecast_by_sku_lite(
    horizon_days: int = Query(14, ge=1, le=60),
    window_days: int = Query(60, ge=7, le=365),
    top_n: int = Query(10, ge=1, le=25),
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_read_db),
):
    """Per-SKU revenue forecast for top-N products by window revenue.

    Closes Gap #6 of brutal $0-70 audit (2026-04-27). Lebesgue $59 +
    Forthcast $19.99 ship per-product forecasts at entry tier; we
    match per parity doctrine, with built-on-top differentiator.

    Each product gets:
      - Holt double-exp smoothed point forecast
      - 80/95% prediction intervals (residual-std-based)
      - Confidence label (high/medium/low/insufficient by n_days + r²)
      - Backtest accuracy_pct = 100 - mean(|residual|/observed) — single
        scalar honesty surface, no $0-60 competitor ships this

    Differentiator (parity doctrine §3 unique-feature axis):
      `biggest_riser` + `biggest_faller` plain-language insight panel —
      single-line reading-grade takeaway ("Stock the riser, investigate
      the faller before inventory builds up").

    Cold-start: products with < 7 days of revenue history get
    confidence="insufficient" and forecast_point=0 (honest, not
    fabricated).
    """
    from app.services.probabilistic_forecast import forecast_by_sku
    return forecast_by_sku(
        db, shop,
        horizon_days=horizon_days,
        window_days=window_days,
        top_n=top_n,
    )
