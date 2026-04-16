"""
customer_churn.py — Per-customer churn prediction API (δ4).

GET /pro/customer-churn?limit=50
    Top N customers most at risk of going silent in the next 30 days.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.customer_churn_scorer import score_shop_customers

router = APIRouter(prefix="/pro", tags=["customer_churn"])




@router.get("/customer-churn")
def list_at_risk_customers(
    limit: int = Query(50, ge=1, le=500),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    scored = score_shop_customers(db, shop, limit=limit)
    summary = {
        "critical": sum(1 for c in scored if c["risk_band"] == "critical"),
        "high": sum(1 for c in scored if c["risk_band"] == "high"),
        "medium": sum(1 for c in scored if c["risk_band"] == "medium"),
        "low": sum(1 for c in scored if c["risk_band"] == "low"),
    }
    return {
        "shop_domain": shop,
        "total_customers_scored": len(scored),
        "by_risk_band": summary,
        "customers": scored,
    }
