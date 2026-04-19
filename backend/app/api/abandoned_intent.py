"""
abandoned_intent.py — GET /pro/abandoned-intent API endpoint.

Returns session-level intent analysis: which products visitors view
but never buy, where they exit, and how buyer vs non-buyer paths differ.

Accessible to all merchant sessions; response fidelity reduces for
non-Pro plans:
  - Pro merchants: full 15-product list + session_insights
  - Starter/Lite:  top 3 products only + session_insights redacted

Cached 3h; cache is tier-agnostic and filter applied at response time.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session
from app.models.merchant import Merchant

router = APIRouter(tags=["abandoned_intent"])


class AbandonedIntentResponse(BaseModel):
    shop_domain: str
    products: list[dict[str, Any]] = Field(default_factory=list)
    session_insights: dict[str, Any] = Field(default_factory=dict)
    headline: str
    # Shop's native currency — loss/price fields in products are native.
    currency: str = "USD"
    generated_at: str


@router.get("/pro/abandoned-intent", response_model=AbandonedIntentResponse)
def get_abandoned_intent(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    """
    Session-level abandoned intent analysis: products with high
    interest but low conversion, exit products, buyer vs non-buyer
    session patterns.

    Plan-aware: Pro gets full list + session_insights, Starter sees
    top 3 products with an upgrade bridge in the UI for the full list.
    """
    from app.services.abandoned_intent import compute_abandoned_intent
    row = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    plan = "pro" if (row is not None and row.plan == "pro" and row.billing_active) else "starter"
    return compute_abandoned_intent(db, shop, plan=plan)
