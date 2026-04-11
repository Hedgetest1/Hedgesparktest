"""
shop_cost_defaults.py — Per-shop global cost assumptions.

One row per shop, merged with pnl_engine module defaults at compute time.
Every numeric column is NULLABLE so merchants can partially-configure:
e.g. set COGS % only, leave shipping at default, leave payment fees at
Shopify Payments standard.

Used by:
  - app/services/pnl_engine.py — as the preferred source before module constants
  - app/api/cost_config.py     — Settings API (GET/PATCH /pro/costs/defaults)
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Numeric, String, text

from app.core.database import Base


class ShopCostDefaults(Base):
    __tablename__ = "shop_cost_defaults"

    shop_domain = Column(String, primary_key=True)

    # Fraction (0.40 = 40%) — when NULL, pnl_engine uses _DEFAULT_COGS_PCT.
    default_cogs_pct = Column(Numeric(6, 4), nullable=True)

    # Flat shipping per order in native currency.
    default_shipping_per_order = Column(Numeric(10, 2), nullable=True)

    # Payment processor rates — configurable per shop (Stripe, Shopify Plus,
    # non-Shopify gateways all have different costs).
    payment_pct  = Column(Numeric(6, 4), nullable=True)
    payment_flat = Column(Numeric(10, 2), nullable=True)

    # Rough monthly ad spend — bridge until Phase 3 OAuth wires Meta + Google.
    ad_spend_manual_monthly = Column(Numeric(12, 2), nullable=True)

    currency = Column(String(8), nullable=True)

    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
    updated_at = Column(DateTime, nullable=False, server_default=text("NOW()"))
