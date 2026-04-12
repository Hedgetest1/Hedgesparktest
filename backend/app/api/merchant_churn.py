"""
merchant_churn.py — GET /ops/churn-report API endpoint.

Returns predictive churn scores for all active merchants.
Operator-only (X-API-Key auth). Not merchant-facing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter(tags=["merchant_churn"])


def _verify_ops_key(x_api_key: str = Header(default="")):
    import os
    expected = os.getenv("OPS_API_KEY", "").strip()
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/ops/churn-report")
def get_churn_report(
    db: Session = Depends(get_db),
    _: None = Depends(_verify_ops_key),
):
    """
    Predictive churn scores for all active merchants.
    Operator-only. Shows at-risk merchants with recommended actions.
    """
    from app.services.merchant_churn_predictor import compute_churn_report
    return compute_churn_report(db)


@router.get("/ops/churn-score/{shop_domain}")
def get_single_churn_score(
    shop_domain: str,
    db: Session = Depends(get_db),
    _: None = Depends(_verify_ops_key),
):
    """Single merchant churn risk score."""
    from app.services.merchant_churn_predictor import compute_churn_score
    return compute_churn_score(db, shop_domain)
