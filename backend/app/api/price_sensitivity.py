"""
price_sensitivity.py — GET /pro/price-sensitivity API endpoint.

Returns behavioral price elasticity analysis: conversion by price band,
products with price barrier signals, sweet spots and ceilings.
Pro-gated. Cached 6h.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session

router = APIRouter(tags=["price_sensitivity"])


@router.get("/pro/price-sensitivity")
def get_price_sensitivity(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Behavioral price elasticity: conversion rates by price band,
    products with price barrier signals (high interest but low CVR),
    sweet spots and price ceilings.
    """
    from app.services.price_sensitivity import compute_price_sensitivity
    return compute_price_sensitivity(db, shop)
