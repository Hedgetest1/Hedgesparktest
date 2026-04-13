"""
anomaly_fusion.py — Phase Ω anomaly fusion API.

  GET /pro/anomalies/fusion — current fused alerts + raw signals
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["anomaly_fusion"])


@router.get("/pro/anomalies/fusion")
def get_fusion(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    from app.services.anomaly_fusion import fuse
    return fuse(db, shop)
