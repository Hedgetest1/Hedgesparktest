"""
cost_config.py — Cost configuration API for Profit Intelligence (Sprint B Phase 2).

Pro merchants use this endpoint family to override the pnl_engine defaults
with their real cost structure. Every setting is optional — partial config
is allowed (e.g. set COGS % only, leave shipping and payment at defaults).

Endpoints
---------
GET    /pro/costs/defaults
    Return the current shop_cost_defaults row. When no row exists yet for
    the shop, returns an all-NULL shape so the frontend can render an empty
    settings form without special-casing.

PATCH  /pro/costs/defaults
    Upsert the shop_cost_defaults row. Accepts any subset of the configurable
    fields — NULL on a field means "use module default". Every non-NULL field
    the merchant provides bumps the pnl precision toward "refined" / "exact".

GET    /pro/costs/products
    Return the merchant's per-product cost rows (for the Settings UI table).
    Ordered by updated_at DESC so the most recently edited rows appear first.

POST   /pro/costs/products
    Bulk upsert per-product COGS rows. Accepts a list of products with
    product_key / cogs_per_unit / shipping_cost_per_unit / currency. Existing
    rows are updated by (shop_domain, product_key); new rows are inserted.

All routes are Pro-only (require_pro_session enforces plan + session cookie).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_pro_session
from app.models.product_cost import ProductCost
from app.models.shop_cost_defaults import ShopCostDefaults
from app.services.shopify_cogs_sync import sync_product_costs_from_shopify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pro/costs", tags=["cost_config"])




# ---------------------------------------------------------------------------
# Response + request models
# ---------------------------------------------------------------------------


class ShopCostDefaultsResponse(BaseModel):
    """GET /pro/costs/defaults — current shop cost assumptions."""
    shop_domain: str
    default_cogs_pct: float | None = None
    default_shipping_per_order: float | None = None
    payment_pct: float | None = None
    payment_flat: float | None = None
    ad_spend_manual_monthly: float | None = None
    currency: str | None = None
    updated_at: str | None = None


class ShopCostDefaultsPatch(BaseModel):
    """PATCH /pro/costs/defaults — partial update payload.

    Every field is optional. NULL or omitted fields are left unchanged in the
    existing row (or inserted as NULL when creating the row).
    """
    default_cogs_pct: float | None = Field(default=None, ge=0, le=1)
    default_shipping_per_order: float | None = Field(default=None, ge=0)
    payment_pct: float | None = Field(default=None, ge=0, le=1)
    payment_flat: float | None = Field(default=None, ge=0)
    ad_spend_manual_monthly: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)


class ProductCostRow(BaseModel):
    """One product cost row."""
    id: int
    product_key: str
    product_title: str | None = None
    cogs_per_unit: float | None = None
    shipping_cost_per_unit: float | None = None
    currency: str | None = None
    source: str
    updated_at: str | None = None


class ProductCostsListResponse(BaseModel):
    """GET /pro/costs/products — list of product cost rows."""
    shop_domain: str
    total: int
    products: list[ProductCostRow]


class ProductCostInput(BaseModel):
    """One row in the bulk-upsert payload."""
    product_key: str = Field(..., min_length=1, max_length=255)
    product_title: str | None = Field(default=None, max_length=255)
    cogs_per_unit: float | None = Field(default=None, ge=0)
    shipping_cost_per_unit: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)


class ProductCostsBulkPayload(BaseModel):
    """POST /pro/costs/products request body — list of rows to upsert."""
    products: list[ProductCostInput] = Field(..., max_length=500)


class ProductCostsBulkResponse(BaseModel):
    """POST /pro/costs/products — upsert result."""
    shop_domain: str
    inserted: int
    updated: int
    total: int


class ShopifyCogsSyncResponse(BaseModel):
    """POST /pro/costs/sync-from-shopify — auto-import result from Admin API."""
    shop_domain: str
    status: str = Field(..., description="'ok' | 'empty' | 'error'")
    reason: str | None = None
    message: str
    products_scanned: int = 0
    variants_scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_cost: int = 0
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Shop cost defaults
# ---------------------------------------------------------------------------


@router.get(
    "/defaults",
    response_model=ShopCostDefaultsResponse,
    response_model_exclude_none=False,
)
def get_shop_cost_defaults(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Return the current shop_cost_defaults row (all-NULL shape if absent)."""
    row = db.query(ShopCostDefaults).filter_by(shop_domain=shop).first()
    if row is None:
        return {
            "shop_domain":                shop,
            "default_cogs_pct":           None,
            "default_shipping_per_order": None,
            "payment_pct":                None,
            "payment_flat":               None,
            "ad_spend_manual_monthly":    None,
            "currency":                   None,
            "updated_at":                 None,
        }

    return _defaults_row_to_dict(row)


@router.patch(
    "/defaults",
    response_model=ShopCostDefaultsResponse,
    response_model_exclude_none=False,
)
def patch_shop_cost_defaults(
    payload: ShopCostDefaultsPatch,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Upsert the shop_cost_defaults row. Any field left as None in the payload
    is preserved from the existing row (or stays NULL on insert).
    """
    row = db.query(ShopCostDefaults).filter_by(shop_domain=shop).first()
    created = False
    if row is None:
        row = ShopCostDefaults(shop_domain=shop)
        db.add(row)
        created = True

    # Only overwrite fields the merchant explicitly provided. We distinguish
    # "field set to None" from "field not in payload" via model_dump(exclude_unset=True).
    patch_data = payload.model_dump(exclude_unset=True)
    for key, value in patch_data.items():
        setattr(row, key, value)

    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("cost_config: defaults upsert failed shop=%s: %s", shop, exc)
        raise HTTPException(status_code=500, detail="Failed to save cost defaults.")

    db.refresh(row)
    log.info("cost_config: defaults %s shop=%s fields=%s",
             "created" if created else "updated", shop, list(patch_data.keys()))
    return _defaults_row_to_dict(row)


# ---------------------------------------------------------------------------
# Per-product costs
# ---------------------------------------------------------------------------


@router.get(
    "/products",
    response_model=ProductCostsListResponse,
    response_model_exclude_none=False,
)
def list_product_costs(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """Return all product_costs rows for the shop, newest-edited first."""
    rows = (
        db.query(ProductCost)
        .filter_by(shop_domain=shop)
        .order_by(ProductCost.updated_at.desc())
        .limit(500)
        .all()
    )
    return {
        "shop_domain": shop,
        "total":       len(rows),
        "products":    [_product_row_to_dict(r) for r in rows],
    }


@router.post(
    "/products",
    response_model=ProductCostsBulkResponse,
    response_model_exclude_none=False,
)
def upsert_product_costs(
    payload: ProductCostsBulkPayload,
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Bulk-upsert product cost rows. Existing rows for (shop, product_key) are
    updated in place; new rows are inserted. Atomic transaction — rolls back
    entirely if any single row fails.
    """
    inserted = 0
    updated  = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        for item in payload.products:
            existing = (
                db.query(ProductCost)
                .filter_by(shop_domain=shop, product_key=item.product_key)
                .first()
            )
            if existing is None:
                db.add(ProductCost(
                    shop_domain=shop,
                    product_key=item.product_key,
                    product_title=item.product_title,
                    cogs_per_unit=item.cogs_per_unit,
                    shipping_cost_per_unit=item.shipping_cost_per_unit,
                    currency=item.currency,
                    source="manual",
                    created_at=now,
                    updated_at=now,
                ))
                inserted += 1
            else:
                if item.product_title is not None:
                    existing.product_title = item.product_title
                if item.cogs_per_unit is not None:
                    existing.cogs_per_unit = item.cogs_per_unit
                if item.shipping_cost_per_unit is not None:
                    existing.shipping_cost_per_unit = item.shipping_cost_per_unit
                if item.currency is not None:
                    existing.currency = item.currency
                existing.updated_at = now
                updated += 1

        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("cost_config: product bulk upsert failed shop=%s: %s", shop, exc)
        raise HTTPException(status_code=500, detail="Failed to save product costs.")

    log.info("cost_config: product bulk upsert shop=%s inserted=%d updated=%d",
             shop, inserted, updated)

    return {
        "shop_domain": shop,
        "inserted":    inserted,
        "updated":     updated,
        "total":       inserted + updated,
    }


# ---------------------------------------------------------------------------
# Shopify Admin API auto-import — pulls real COGS from inventory_items.cost
# ---------------------------------------------------------------------------


@router.post(
    "/sync-from-shopify",
    response_model=ShopifyCogsSyncResponse,
    response_model_exclude_none=False,
)
def sync_costs_from_shopify(
    shop: str = Depends(require_pro_session),
    db: Session = Depends(get_db),
):
    """
    Auto-import product COGS from the Shopify Admin API.

    Reads `inventory_items.cost` for every product variant in the shop and
    upserts into `product_costs`. Idempotent — safe to call multiple times.
    Never overwrites manual merchant entries; only updates rows whose source
    is already `shopify_admin_api` or has no cost set.

    Requires the existing Shopify install token (no new OAuth scope needed —
    `read_products` is part of the base install).

    Pro-only: require_pro_session enforces plan + session cookie.
    """
    return sync_product_costs_from_shopify(db, shop)


# ---------------------------------------------------------------------------
# Row → dict helpers
# ---------------------------------------------------------------------------


def _defaults_row_to_dict(row: ShopCostDefaults) -> dict:
    def _f(v):
        if v is None:
            return None
        return float(v)

    return {
        "shop_domain":                row.shop_domain,
        "default_cogs_pct":           _f(row.default_cogs_pct),
        "default_shipping_per_order": _f(row.default_shipping_per_order),
        "payment_pct":                _f(row.payment_pct),
        "payment_flat":               _f(row.payment_flat),
        "ad_spend_manual_monthly":    _f(row.ad_spend_manual_monthly),
        "currency":                   row.currency,
        "updated_at":                 row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _product_row_to_dict(row: ProductCost) -> dict:
    def _f(v):
        if v is None:
            return None
        return float(v)

    return {
        "id":                     row.id,
        "product_key":            row.product_key,
        "product_title":          row.product_title,
        "cogs_per_unit":          _f(row.cogs_per_unit),
        "shipping_cost_per_unit": _f(row.shipping_cost_per_unit),
        "currency":               row.currency,
        "source":                 row.source,
        "updated_at":             row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }
