"""
outbound_webhook.py — Phase Ω ecosystem #1.

Merchant-subscribable outbound webhook system. Mirrors Stripe / Shopify's
own webhook UX so merchants can plug HedgeSpark events into their existing
Zapier / n8n / custom backends.

Two tables:
  * outbound_webhook_subscriptions — one row per (shop, target_url, event_types)
  * outbound_webhook_deliveries    — one row per delivery attempt (audit)

Signing: HMAC-SHA256(secret, body) → header `X-HedgeSpark-Signature`.
Replay protection: timestamp header + 5-minute skew window enforced
client-side. Same envelope shape as Shopify webhooks for familiarity.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class OutboundWebhookSubscription(Base):
    __tablename__ = "outbound_webhook_subscriptions"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)

    target_url = Column(String(1024), nullable=False)
    secret = Column(String(128), nullable=False)  # signing secret, generated on create

    # JSONB list of event types this subscription wants:
    # ["nudge.fired", "rars.spike", "goal.at_risk", "anomaly.detected", ...]
    event_types = Column(JSONB, nullable=False, default=list)

    status = Column(String(16), nullable=False, default="active")  # active|paused|disabled

    # Health
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    auto_disabled = Column(Boolean, nullable=False, default=False)

    description = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_outbound_wh_sub_shop_status", "shop_domain", "status"),
    )


class OutboundWebhookDelivery(Base):
    __tablename__ = "outbound_webhook_deliveries"

    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey("outbound_webhook_subscriptions.id"), nullable=False, index=True)
    shop_domain = Column(String, nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    event_id = Column(String(64), nullable=False, index=True)  # idempotency key
    payload = Column(JSONB, nullable=False)

    status = Column(String(16), nullable=False, default="pending")  # pending|delivered|failed|dead
    attempts = Column(Integer, nullable=False, default=0)
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_attempted_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_outbound_wh_del_status_attempt", "status", "last_attempted_at"),
    )
