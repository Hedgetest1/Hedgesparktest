"""
shop_order.py — Real Shopify order record.

Ingested from Shopify's orders/updated webhook.  Replaces the hardcoded AOV
fallback (DEFAULT_AOV = 50.0) and inferred conversion probability pipeline
as the source of truth for all per-merchant revenue calculations.

Why this exists
---------------
Before this model, WishSpark computed every revenue figure (expected_loss,
urgency_score, loss_band) from:
    views_24h × inferred_conversion_probability × 50.0  (hardcoded AOV)

That chain is an engineering estimate, not a business metric.  shop_orders
gives us real order value per shop so:
  - AOV becomes computed: AVG(total_price) WHERE shop_domain = shop
  - Conversion rate becomes real: COUNT(orders) / COUNT(unique_sessions)
  - Revenue attribution becomes possible: link orders back to visitor sessions
  - Feedback loop becomes possible: measure metric delta after action completion

Ingestion
---------
POST /webhooks/shopify/orders receives the Shopify orders/updated webhook
payload and upserts a row here via app.services.order_ingestion.

Idempotency
-----------
shopify_order_id has a UNIQUE constraint.  Duplicate webhook deliveries (Shopify
guarantees at-least-once, not exactly-once) are silently ignored.

Schema notes
------------
line_items — stored as JSONB for flexibility.  Each item is the raw Shopify
    line item object: {id, product_id, variant_id, title, quantity, price, sku}.
    Enables per-product revenue attribution without a normalised line_items table
    in v1.  Migrate to a normalised table once query patterns are established.

customer_id — nullable.  Guest checkouts have no Shopify customer record.
    When present, enables multi-order LTV computation.

currency — stored per-order because a multi-currency shop can have mixed
    order currencies.  AOV computation must group by currency.

created_at — Shopify-side timestamp, not server ingestion time.  Used for
    time-scoped analytics (daily/weekly revenue windows).  ingested_at records
    the server receipt time for audit and dedup purposes.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class ShopOrder(Base):
    __tablename__ = "shop_orders"

    id = Column(Integer, primary_key=True)

    # Tenant scope — all reads must filter by shop_domain
    shop_domain = Column(String, nullable=False)

    # Shopify's own order ID — globally unique across all Shopify shops
    shopify_order_id = Column(String, nullable=False)

    # Revenue fields
    total_price = Column(Numeric(18, 2), nullable=False)
    currency    = Column(String, nullable=False, default="EUR", server_default="EUR")

    # Optional customer link — NULL for guest checkouts
    customer_id    = Column(String, nullable=True)

    # Customer email from the order — NULL for guest checkouts without email.
    # Used for cohort retention analysis and Klaviyo identity resolution.
    customer_email = Column(String, nullable=True)

    # Raw Shopify line items array: [{id, product_id, variant_id, title, quantity, price, sku}, ...]
    # REPLACE WITH REAL ORDER DATA: query this column to compute per-product revenue attribution
    line_items  = Column(JSONB, nullable=False, default=list, server_default=text("'[]'"))

    # Shopify-side order creation timestamp — use for revenue time-window queries
    created_at  = Column(DateTime, nullable=False)

    # Server-side ingestion timestamp — use for dedup auditing, not analytics
    ingested_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default=text("now()"))

    # Ingestion source: "pixel" (client-side Custom Pixel) or "webhook" (Shopify Admin API)
    # Pixel rows have line_items=[] and customer_id/email=None.
    # Webhook rows (when available) carry full order data.
    source = Column(String(16), nullable=False, server_default="pixel")

    # Class D base-analytics columns (2026-04-26 — competitor parity vs $0-70).
    # All NULLABLE: enrichment is forward-looking, populated by spark-pixel.js v14+
    # from Shopify checkout context. Old orders stay NULL; endpoints surface
    # has_data=false until enriched orders accumulate.
    discount_amount    = Column(Numeric(12, 2), nullable=True)
    discount_codes     = Column(JSONB,           nullable=True)  # ["SUMMER10", "FREESHIP"]
    tax_amount         = Column(Numeric(12, 2), nullable=True)
    payment_method     = Column(String(64),      nullable=True)
    financial_status   = Column(String(32),      nullable=True)  # paid / pending / authorized / refunded
    fulfillment_status = Column(String(32),      nullable=True)  # fulfilled / unfulfilled / partial

    __table_args__ = (
        # Idempotency: duplicate webhook deliveries are caught at DB level
        UniqueConstraint("shopify_order_id", name="uq_shop_orders_shopify_order_id"),
        # Per-shop order listing and AOV computation
        Index("ix_shop_orders_shop_domain", "shop_domain"),
        # Time-scoped revenue queries: WHERE shop_domain = X AND created_at > Y
        Index("ix_shop_orders_shop_created", "shop_domain", "created_at"),
        Index("ix_shop_orders_customer_email", "shop_domain", "customer_email"),
    )
