"""
product_cost.py — Per-product real COGS and shipping.

When a row exists with non-NULL cogs_per_unit, pnl_engine uses it in place
of the shop-level default for orders whose line_items match product_key.

product_key is the same identifier the rest of the codebase uses:
  - Shopify numeric product_id as a string (preferred, stable across renames)
  - Canonical /products/{handle} path (fallback for pre-webhook orders)

Used by:
  - app/services/pnl_engine.py — as the preferred source for per-order COGS
  - app/api/cost_config.py     — Settings API (GET /pro/costs/products etc)
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)

from app.core.database import Base


class ProductCost(Base):
    __tablename__ = "product_costs"
    __table_args__ = (
        UniqueConstraint(
            "shop_domain", "product_key",
            name="uq_product_costs_shop_product",
        ),
        Index("ix_product_costs_shop_product", "shop_domain", "product_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    shop_domain = Column(String, nullable=False, index=True)
    product_key = Column(String, nullable=False)

    # Denormalized for display — avoids a join in the Settings UI.
    product_title = Column(String, nullable=True)

    cogs_per_unit          = Column(Numeric(10, 2), nullable=True)
    shipping_cost_per_unit = Column(Numeric(10, 2), nullable=True)

    currency = Column(String(8), nullable=True)

    # Provenance — "manual" | "csv_import" | "shopify_admin_api"
    source = Column(String(32), nullable=False, server_default=text("'manual'"))

    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
