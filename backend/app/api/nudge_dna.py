"""
nudge_dna.py — Winning nudge pattern API (δ5).

GET /pro/nudge-dna?window_days=30
    Feature-lift ranking + top variants + composer lessons.

POST /pro/nudge-dna/refresh
    Force recompute (bypasses 4h cache).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.nudge_dna import extract_patterns, get_cached_dna

router = APIRouter(prefix="/pro", tags=["nudge_dna"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/nudge-dna")
def get_nudge_dna(
    window_days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    cached = get_cached_dna(shop)
    if cached is not None:
        return cached
    return extract_patterns(db, shop, window_days=window_days)


@router.post("/nudge-dna/refresh")
def refresh_nudge_dna(
    window_days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    return extract_patterns(db, shop, window_days=window_days)
