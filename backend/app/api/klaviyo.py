"""
klaviyo.py — Klaviyo behavioral segment integration (Pro only).

GET  /pro/klaviyo/segment?shop=&product_url=&hours=
    Returns the HOT segment for a product with identity resolution.
    Shows how many visitors are identifiable (have purchased before)
    and how many are anonymous.

POST /pro/klaviyo/push?shop=
    Pushes identified HOT visitors to Klaviyo Events API.
    Uses the merchant's stored Klaviyo key (from Settings → Integrations).
    Body: {"product_url": str, "hours"?: int}

    Legacy: still accepts optional klaviyo_private_key in body for
    backward compatibility. If omitted, uses the stored key.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
from app.services.klaviyo_export import (
    get_segment_with_identity,
    push_segment_to_klaviyo,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/klaviyo", tags=["klaviyo"])




class KlaviyoPushRequest(BaseModel):
    product_url:          str   = Field(..., min_length=1, max_length=2048)
    klaviyo_private_key:  str | None = Field(default=None, min_length=10, max_length=256)  # legacy — use stored key when omitted
    hours:                int   = Field(default=72, ge=1, le=168)


@router.get("/segment")
def get_klaviyo_segment(
    product_url: str,
    hours: int = 72,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    HOT segment with identity resolution for Klaviyo export preview.

    Shows:
    - How many HOT visitors are identifiable (email from purchase history)
    - How many are anonymous (no email available)
    - The revenue window estimate
    - Preview of what would be pushed to Klaviyo

    Use this before /push to see what data will be exported.
    """
    result = get_segment_with_identity(db, shop, product_url, hours)
    return result


@router.post("/push")
def push_to_klaviyo(
    body: KlaviyoPushRequest,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Push identified HOT visitors to Klaviyo as custom events.

    Each visitor receives a "WishSpark — High Intent Signal" event in Klaviyo.
    Merchants can use this in Klaviyo flows to trigger email campaigns.

    Uses the merchant's stored Klaviyo key by default. An explicit key in the
    request body overrides the stored key (legacy support).

    Returns: {"pushed": int, "anonymous": int, "errors": int}
    """
    # Resolve key: explicit body key (legacy) > stored merchant key
    klaviyo_key = body.klaviyo_private_key
    if not klaviyo_key:
        from app.services.klaviyo_connection import resolve_klaviyo_key
        klaviyo_key = resolve_klaviyo_key(db, shop)
    if not klaviyo_key:
        raise HTTPException(
            status_code=400,
            detail="Klaviyo not connected — save your API key in Settings → Integrations",
        )

    result = push_segment_to_klaviyo(
        db=db,
        shop_domain=shop,
        product_url=body.product_url,
        klaviyo_private_key=klaviyo_key,
        hours=body.hours,
    )

    log.info(
        "klaviyo: push complete shop=%s product=%s pushed=%d anonymous=%d errors=%d",
        shop, body.product_url,
        result["pushed"], result["anonymous"], result["errors"],
    )

    return result
