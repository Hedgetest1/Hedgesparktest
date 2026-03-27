"""
shopify_admin_api.py — Shopify Admin API execution endpoints (Pro only).

These endpoints let WishSpark act on behalf of the merchant — reading real
inventory data and executing actions directly in the Shopify admin.

All endpoints require Pro plan (require_pro_plan).
All mutations (discount creation, price updates) are logged for audit.

GET  /pro/shopify/inventory?shop=&product_url=
    Read real inventory levels for a product.

POST /pro/shopify/discount?shop=
    Create a percentage discount code.
    Body: {"title", "percentage", "code", "product_ids"?, "usage_limit"?}

POST /pro/shopify/price?shop=
    Update a product variant price.
    Body: {"variant_id", "new_price"}

GET  /pro/shopify/products?shop=&limit=
    Fetch products from the merchant's store (catalog enrichment).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.deps import require_pro_session
from app.services.shopify_admin import (
    create_discount,
    get_product_inventory,
    get_shop_products,
    update_product_price,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/shopify", tags=["shopify-admin"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class DiscountRequest(BaseModel):
    title:       str   = Field(..., min_length=1, max_length=128)
    percentage:  float = Field(..., gt=0, le=100)
    code:        str   = Field(..., min_length=1, max_length=64)
    product_ids: Optional[list[int]] = None
    usage_limit: int   = Field(default=100, ge=1, le=10000)


class PriceUpdateRequest(BaseModel):
    variant_id: int
    new_price:  str = Field(..., min_length=1, max_length=20,
                             pattern=r"^\d+(\.\d{1,2})?$")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/inventory")
def get_inventory(
    product_url: str,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Read real inventory levels for a product from Shopify Admin API.

    Returns live stock data — the foundation for truthful scarcity nudges.
    Requires Shopify OAuth access_token for this shop.
    """
    result = get_product_inventory(db, shop, product_url)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Product not found or Shopify Admin API unavailable. "
                   "Ensure this shop has completed OAuth installation.",
        )
    return result


@router.post("/discount")
def create_discount_code(
    body: DiscountRequest,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Create a percentage discount code in Shopify.

    Example body:
        {"title": "Flash Sale 10%", "percentage": 10.0, "code": "FLASH10"}

    Scoped to specific products when product_ids is provided.
    """
    result = create_discount(
        db=db,
        shop_domain=shop,
        title=body.title,
        percentage=body.percentage,
        code=body.code.upper(),
        product_ids=body.product_ids,
        usage_limit=body.usage_limit,
    )
    if result is None:
        raise HTTPException(
            status_code=422,
            detail="Failed to create discount code. Check Shopify Admin API access and code uniqueness.",
        )

    log.info(
        "shopify_admin_api: discount created shop=%s code=%s pct=%.1f%%",
        shop, result["code"], body.percentage,
    )
    return result


@router.post("/price")
def update_price(
    body: PriceUpdateRequest,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Update a product variant's price in Shopify.

    Example body:
        {"variant_id": 12345678, "new_price": "24.99"}

    Prices are set at the variant level.  Use GET /pro/shopify/products
    to discover variant IDs.
    """
    success = update_product_price(
        db=db,
        shop_domain=shop,
        variant_id=body.variant_id,
        new_price=body.new_price,
    )
    if not success:
        raise HTTPException(
            status_code=422,
            detail="Failed to update price. Check variant ID and Shopify Admin API access.",
        )

    log.info(
        "shopify_admin_api: price updated shop=%s variant_id=%d new_price=%s",
        shop, body.variant_id, body.new_price,
    )
    return {"updated": True, "variant_id": body.variant_id, "new_price": body.new_price}


@router.get("/products")
def list_products(
    limit: int = 10,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Fetch products from the merchant's Shopify store.

    Returns product catalog with variant IDs — useful for wiring up
    discount and price actions to specific products.
    """
    products = get_shop_products(db, shop, limit=min(limit, 50))
    return {"products": products, "count": len(products)}
