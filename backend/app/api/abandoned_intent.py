"""
abandoned_intent.py — GET /pro/abandoned-intent API endpoint.

Returns session-level intent analysis: which products visitors view
but never buy, where they exit, and how buyer vs non-buyer paths differ.
Pro-gated. Cached 3h.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["abandoned_intent"])


@router.get("/pro/abandoned-intent")
def get_abandoned_intent(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Session-level abandoned intent analysis: products with high
    interest but low conversion, exit products, buyer vs non-buyer
    session patterns.
    """
    from app.services.abandoned_intent import compute_abandoned_intent
    return compute_abandoned_intent(db, shop)
