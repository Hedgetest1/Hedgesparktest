"""
nudge_dna.py — Winning nudge pattern API (δ5).

GET /pro/nudge-dna?window_days=30
    Feature-lift ranking + top variants + composer lessons (4h cache).

POST /pro/nudge-dna/refresh
    Force recompute (bypasses 4h cache).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_scale_session
from app.services.nudge_dna import extract_patterns, get_cached_dna

router = APIRouter(prefix="/pro", tags=["nudge_dna"])


class NudgeDnaFeature(BaseModel):
    feature: str
    with_true_rate: float
    with_false_rate: float
    lift_pct: float
    sample_with: int
    sample_without: int
    significance: str


class NudgeDnaVariant(BaseModel):
    variant_key: str
    copy_text: str
    conversion_rate: float
    impressions: int
    conversions: int


class NudgeDnaResponse(BaseModel):
    shop_domain: str
    window_days: int
    total_impressions: int
    total_conversions: int
    overall_conversion_rate: float
    features: list[NudgeDnaFeature] = Field(default_factory=list)
    top_variants: list[NudgeDnaVariant] = Field(default_factory=list)
    lessons_for_composer: list[str] = Field(default_factory=list)
    generated_at: str | None = None
    status: str | None = None


@router.get("/nudge-dna", response_model=NudgeDnaResponse)
def get_nudge_dna(
    window_days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_read_db),
):
    cached = get_cached_dna(shop)
    if cached is not None:
        return cached
    return extract_patterns(db, shop, window_days=window_days)


@router.post("/nudge-dna/refresh", response_model=NudgeDnaResponse)
def refresh_nudge_dna(
    window_days: int = Query(30, ge=7, le=180),
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_db),
):
    """Force recompute — bypasses the 4h Redis cache and returns fresh patterns."""
    return extract_patterns(db, shop, window_days=window_days)
