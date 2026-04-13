"""
causal_explainer.py — Phase Ω causal "why" engine API.

  GET /pro/causal/explain — ranked causal hypotheses + recommended action
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["causal_explainer"])


@router.get("/pro/causal/explain")
def get_explain(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.causal_explainer import explain
    return explain(db, shop)
