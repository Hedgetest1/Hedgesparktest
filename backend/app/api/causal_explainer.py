"""
causal_explainer.py — Phase Ω causal "why" engine API.

  GET /pro/causal/explain — ranked causal hypotheses + recommended action
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_scale_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["causal_explainer"])


class CausalExplainResponse(BaseModel):
    shop_domain: str
    vertical: str | None = None
    vertical_display: str | None = None
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str
    next_action: str | None = None
    fusion_alerts: list[dict[str, Any]] = Field(default_factory=list)
    raw_signals: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str


@router.get("/pro/causal/explain", response_model=CausalExplainResponse)
def get_explain(
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    from app.services.causal_explainer import explain
    return explain(db, shop)
