"""
klaviyo.py — Klaviyo behavioral segment integration (Pro only).

GET  /pro/klaviyo/segment?shop=&product_url=&hours=
    Returns the HOT segment for a product with identity resolution.
    Shows how many visitors are identifiable (have purchased before)
    and how many are anonymous.

POST /pro/klaviyo/push?shop=
    Pushes identified HOT visitors to Klaviyo Events API.
    Requires the merchant's Klaviyo Private API Key in the request body.
    Body: {"product_url": str, "klaviyo_private_key": str, "hours"?: int}

The Klaviyo Private Key is passed per-request (not stored server-side in v1).
Merchants get this from: Klaviyo Account → Settings → API Keys → Private API Key
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_plan
from app.services.klaviyo_export import (
    get_segment_with_identity,
    push_segment_to_klaviyo,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/klaviyo", tags=["klaviyo"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class KlaviyoPushRequest(BaseModel):
    product_url:          str   = Field(..., min_length=1)
    klaviyo_private_key:  str   = Field(..., min_length=10)
    hours:                int   = Field(default=72, ge=1, le=168)


@router.get("/segment")
def get_klaviyo_segment(
    product_url: str,
    hours: int = 72,
    shop: str = Depends(require_pro_plan),
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
    shop: str = Depends(require_pro_plan),
    db: Session = Depends(get_db),
):
    """
    Push identified HOT visitors to Klaviyo as custom events.

    Each visitor receives a "WishSpark — High Intent Signal" event in Klaviyo.
    Merchants can use this in Klaviyo flows to trigger email campaigns.

    Requirements:
    - Merchant must have Klaviyo installed on their store
    - Klaviyo Private API Key required (from Klaviyo Account → Settings → API Keys)
    - Visitors must have previously purchased for identity resolution

    Returns: {"pushed": int, "anonymous": int, "errors": int}
    """
    if len(body.klaviyo_private_key) < 10:
        raise HTTPException(status_code=422, detail="Invalid Klaviyo private key.")

    result = push_segment_to_klaviyo(
        db=db,
        shop_domain=shop,
        product_url=body.product_url,
        klaviyo_private_key=body.klaviyo_private_key,
        hours=body.hours,
    )

    log.info(
        "klaviyo: push complete shop=%s product=%s pushed=%d anonymous=%d errors=%d",
        shop, body.product_url,
        result["pushed"], result["anonymous"], result["errors"],
    )

    return result
