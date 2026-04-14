"""
anomaly_fusion.py — Phase Ω anomaly fusion API.

  GET /pro/anomalies/fusion — current fused alerts + raw signals
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["anomaly_fusion"])


class FusionAlertRow(BaseModel):
    pattern: str
    fusion_score: float
    severity: str
    contributors: list[dict[str, Any]] = Field(default_factory=list)
    window_hours: int
    recommended_action: str
    narrative: str
    detected_at: str


class AtomicSignalRow(BaseModel):
    name: str
    severity: float
    value: float
    baseline: float
    delta_pct: float
    window_hours: int


class AnomalyFusionResponse(BaseModel):
    shop_domain: str
    alerts: list[FusionAlertRow] = Field(default_factory=list)
    atomic_signals: list[AtomicSignalRow] = Field(default_factory=list)
    generated_at: str | None = None


@router.get("/pro/anomalies/fusion", response_model=AnomalyFusionResponse)
def get_fusion(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.anomaly_fusion import fuse
    return fuse(db, shop)
