"""
revenue_autopsy.py — GET /pro/revenue-autopsy API endpoint.

Returns the product-level "why did revenue change" analysis.
Pro-gated. Cached 3h via app.services.revenue_autopsy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["revenue_autopsy"])


@router.get("/pro/revenue-autopsy")
def get_revenue_autopsy(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Product-level revenue change decomposition:
    traffic delta + conversion delta + value delta per product.
    """
    from app.services.revenue_autopsy import compute_product_autopsy
    return compute_product_autopsy(db, shop)
