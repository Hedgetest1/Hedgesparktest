"""
prediction_accuracy.py — GET /pro/prediction-accuracy

The competitive moat endpoint (MA-1). Returns per-metric MAPE + last
N predictions for the calling merchant, OR an honest
`insufficient_history` shape before we have enough matured predictions
to publish a trustworthy number. No competitor in the SMB Shopify
analytics band publishes their accuracy — because doing so admits
what their forecasts actually do vs promise. Ours is a public receipt.

Contract
--------
Response body mirrors `compute_accuracy()`:
    {
        "status": "ok" | "insufficient_history" | "error",
        "metrics": { metric: { sample_size, mape_pct, median_error_pct,
                               currency, last_predictions } },
        "predictions_seen"?: int,           # only if insufficient
        "unlock_at"?: int,                   # only if insufficient
        "message"?: str                      # honest copy for UI
    }

Auth: requires Pro session (via merchant_session dependency). Non-Pro
callers get 403. We deliberately do NOT serve this to Lite — accuracy
receipts are a Pro moat.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
from app.services.prediction_log import MIN_PREDICTIONS_FOR_REPORT, compute_accuracy

router = APIRouter(prefix="/pro", tags=["pro"])


class PredictionEntry(BaseModel):
    prediction_date: str | None
    horizon_date: str | None
    predicted: float
    actual: float
    error_pct: float
    currency: str


class PerMetricAccuracy(BaseModel):
    sample_size: int
    mape_pct: float
    median_error_pct: float
    currency: str
    last_predictions: list[PredictionEntry]


class PredictionAccuracyResponse(BaseModel):
    status: str
    metrics: dict[str, PerMetricAccuracy] = {}
    predictions_seen: int | None = None
    unlock_at: int | None = None
    message: str | None = None


@router.get("/prediction-accuracy", response_model=PredictionAccuracyResponse)
def get_prediction_accuracy(
    shop_domain: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
) -> PredictionAccuracyResponse:
    """Return honest MAPE + last 8 predictions, or insufficient_history
    with unlock copy. Pro-tier only (require_pro_session enforces both
    auth + plan)."""
    report = compute_accuracy(db, shop_domain)
    # Guarantee unlock_at is always present on insufficient responses so
    # the frontend can always render the lock copy uniformly.
    if report.get("status") == "insufficient_history" and "unlock_at" not in report:
        report["unlock_at"] = MIN_PREDICTIONS_FOR_REPORT
    return PredictionAccuracyResponse(**report)
