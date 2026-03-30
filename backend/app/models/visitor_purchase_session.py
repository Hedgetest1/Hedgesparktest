"""
visitor_purchase_session.py — Visitor-to-order attribution bridge.

Each row records that a specific WishSpark visitor (identified by the
persistent localStorage UUID from spark-tracker.js) generated a specific
Shopify order (identified by shopify_order_id from the orders/updated webhook).

The attribution event is fired by spark-attribution.js on the Shopify Order
Status (thank-you) page.

Attribution columns:
    first_source, first_campaign — snapshot of first-touch attribution at conversion time
    last_source, last_campaign — snapshot of last-touch attribution at conversion time
    attribution_evidence — JSON audit trail of the full attribution chain

Joins
-----
To behavioral events (what did this visitor do before buying?):
    SELECT *
    FROM events
    WHERE visitor_id = :visitor_id
      AND shop_domain = :shop_domain
      AND timestamp < :confirmed_at_epoch
    ORDER BY timestamp DESC

To order revenue and line items:
    SELECT *
    FROM shop_orders
    WHERE shopify_order_id = :shopify_order_id

Idempotency
-----------
shopify_order_id has a UNIQUE constraint.  Duplicate attributions from page
refreshes or multiple script deliveries are caught at the DB level and
logged as duplicate skipped — they never produce multiple rows.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.core.database import Base


class VisitorPurchaseSession(Base):
    __tablename__ = "visitor_purchase_sessions"

    id = Column(Integer, primary_key=True)

    # Tenant scope — every query must include shop_domain
    shop_domain = Column(String, nullable=False)

    # Persistent visitor UUID from localStorage ("hedgespark_visitor_id").
    # Matches events.visitor_id and visitors.visitor_id.
    visitor_id = Column(String, nullable=False)

    # Shopify's order ID.  Matches shop_orders.shopify_order_id.
    shopify_order_id = Column(String, nullable=False)

    # Last attributed product before purchase — NULL in v1.
    # Future: populate from events at attribution time or from order line_items.
    product_url = Column(String, nullable=True)

    # Browser-side timestamp when the thank-you page script fired.
    confirmed_at = Column(DateTime, nullable=False)

    # Server receipt time — for audit and lag measurement.
    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # First-touch attribution snapshot (resolved at conversion time)
    first_source = Column(String(64), nullable=True)       # first event's source_type
    first_campaign = Column(String(256), nullable=True)     # first event's utm_campaign

    # Last-touch attribution snapshot (resolved at conversion time)
    last_source = Column(String(64), nullable=True)         # last event's source_type before purchase
    last_campaign = Column(String(256), nullable=True)      # last event's utm_campaign before purchase

    # Full attribution audit trail — JSON with first/last touch details
    attribution_evidence = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("shopify_order_id", name="uq_vps_shopify_order_id"),
        Index("ix_vps_shop_visitor",   "shop_domain", "visitor_id"),
        Index("ix_vps_shop_order",     "shop_domain", "shopify_order_id"),
        Index("ix_vps_shop_confirmed", "shop_domain", "confirmed_at"),
    )
