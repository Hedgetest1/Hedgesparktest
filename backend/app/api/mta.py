"""
mta.py — Multi-Touch Attribution API.

GET /pro/mta?model={first_touch|last_touch|linear|time_decay|position_based}&window_days=30
    Single-model attribution output.

GET /pro/mta/compare?window_days=30
    All 5 models side-by-side + swing matrix + headline insight.
    THE killer view — shows merchants how dramatically attribution
    changes their ROAS perception.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.core.deps import require_pro_session
from app.services.mta_engine import compute_mta, compare_models, _MODELS

router = APIRouter(prefix="/pro", tags=["mta"])




class MtaSource(BaseModel):
    source: str
    touches: int
    revenue_credit_eur: float
    order_fractions: float
    first_touches: int
    last_touches: int
    revenue_share_pct: float


class MtaResponse(BaseModel):
    shop_domain: str
    model: str
    window_days: int
    total_revenue_eur: float
    total_orders: int
    avg_touches_per_journey: float
    sources: list[MtaSource]
    path_samples: list[str]
    generated_at: str


class MtaCompareResponse(BaseModel):
    shop_domain: str
    window_days: int
    matrix: list[dict]
    total_revenue_eur: float
    total_orders: int
    headline: str | None
    generated_at: str


@router.get("/mta", response_model=MtaResponse)
def get_mta(
    model: str = Query("position_based"),
    window_days: int = Query(30, ge=1, le=365),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    if model not in _MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"invalid model: {model}. options: {list(_MODELS.keys())}",
        )
    data = compute_mta(db, shop, model=model, window_days=window_days)  # type: ignore
    return MtaResponse(**data)


@router.get("/mta/compare", response_model=MtaCompareResponse)
def compare_mta(
    window_days: int = Query(30, ge=1, le=365),
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_read_db),  # ε1
):
    data = compare_models(db, shop, window_days=window_days)
    # Strip 'by_model' from response (it's ~5× redundant with matrix)
    return MtaCompareResponse(
        shop_domain=data["shop_domain"],
        window_days=data["window_days"],
        matrix=data["matrix"],
        total_revenue_eur=data["total_revenue_eur"],
        total_orders=data["total_orders"],
        headline=data["headline"],
        generated_at=data["generated_at"],
    )
