"""
storefront_preview.py — Phase Ω'' demo pre-signup API.

  POST /public/preview — public scrape endpoint (no auth, rate-limited)
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["storefront_preview"])


class PreviewIn(BaseModel):
    domain: str = Field(..., min_length=2, max_length=255)


@router.post("/public/preview")
def post_preview(payload: PreviewIn):
    from app.services.storefront_preview import preview
    return preview(payload.domain)
