"""
nudge_event.py — NudgeEvent model for nudge exposure and interaction measurement.

Each row is one measurement event for one nudge × visitor pair:

  event_type = "shown"     — nudge was rendered to this visitor
  event_type = "dismissed" — visitor clicked the dismiss (×) button
  event_type = "clicked"   — visitor clicked a CTA (future nudge types)

This table is the attribution bridge between nudge delivery and purchase outcomes.
visitor_id matches events.visitor_id and visitor_purchase_sessions.visitor_id —
enabling observational post-exposure attribution without storing PII.

nudge_id references active_nudges.id — non-enforced FK so historical measurement
events survive nudge expiry or deactivation.

visitor_id is nullable: NULL when localStorage is blocked on the storefront.
Events with NULL visitor_id count toward aggregate exposure totals but are
excluded from attribution joins that require visitor identity.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class NudgeEvent(Base):
    __tablename__ = "nudge_events"

    id          = Column(Integer,  primary_key=True, autoincrement=True)

    # Tenant scope — every query must include shop_domain
    shop_domain = Column(String,   nullable=False)

    # Which nudge — matches active_nudges.id (non-enforced FK)
    nudge_id    = Column(Integer,  nullable=False)

    # Pseudonymous visitor UUID — null when localStorage blocked
    visitor_id  = Column(String,   nullable=True)

    # Product page where the event occurred — canonical /products/{handle}
    product_url = Column(String,   nullable=False)

    # shown | dismissed | clicked
    event_type  = Column(String,   nullable=False)

    # Server receipt time (UTC) — used as the exposure timestamp for attribution
    created_at  = Column(DateTime, nullable=False, default=utc_now_naive, server_default=text("now()"))

    # Optional JSON payload for future extensibility
    # v1: stores copy_variant at time of shown event
    # Named event_meta (not metadata) — "metadata" is reserved by SQLAlchemy Declarative API
    event_meta  = Column(Text,     nullable=True)

    __table_args__ = (
        # Primary stats query: counts by event_type for one nudge
        Index("ix_nudge_events_shop_nudge_type",
              "shop_domain", "nudge_id", "event_type"),
        # Attribution join: all nudge exposures for one visitor across all nudges
        Index("ix_nudge_events_shop_visitor",
              "shop_domain", "visitor_id"),
        # Time-window queries
        Index("ix_nudge_events_created_at", "created_at"),
        # Per-shop time-window queries (retention, digest)
        Index("ix_nudge_events_shop_created", "shop_domain", "created_at"),
    )

    def metadata_dict(self) -> dict:
        if not self.event_meta:
            return {}
        try:
            return json.loads(self.event_meta)
        except Exception:
            return {}
