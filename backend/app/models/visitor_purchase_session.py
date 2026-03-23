"""
visitor_purchase_session.py — Visitor-to-order attribution bridge.

Each row records that a specific WishSpark visitor (identified by the
persistent localStorage UUID from spark-tracker.js) generated a specific
Shopify order (identified by shopify_order_id from the orders/paid webhook).

The attribution event is fired by spark-attribution.js on the Shopify Order
Status (thank-you) page.

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

To product behavior for conversion profiling:
    SELECT e.product_url,
           AVG(e.max_scroll_depth)  AS avg_scroll,
           AVG(e.dwell_seconds)     AS avg_dwell,
           COUNT(*)                 AS visit_count
    FROM events e
    JOIN visitor_purchase_sessions vps
      ON e.visitor_id = vps.visitor_id AND e.shop_domain = vps.shop_domain
    WHERE vps.shop_domain  = :shop
      AND e.product_url    = :product_url
      AND e.event_type     IN ('product_view', 'dwell_time')
    GROUP BY e.product_url

Idempotency
-----------
shopify_order_id has a UNIQUE constraint.  Duplicate attributions from page
refreshes or multiple script deliveries are caught at the DB level and
logged as duplicate skipped — they never produce multiple rows.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

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

    __table_args__ = (
        UniqueConstraint("shopify_order_id", name="uq_vps_shopify_order_id"),
        Index("ix_vps_shop_visitor",   "shop_domain", "visitor_id"),
        Index("ix_vps_shop_order",     "shop_domain", "shopify_order_id"),
        Index("ix_vps_shop_confirmed", "shop_domain", "confirmed_at"),
    )
