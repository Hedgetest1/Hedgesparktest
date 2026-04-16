"""
EmailEvent — append-only log of Resend delivery events.

Populated by POST /webhooks/resend/events webhook.
Each row maps to a single Resend event (delivered, opened, clicked, bounced, complained).

resend_email_id links to merchant_emails.resend_id for cross-referencing.
shop_domain is resolved at ingestion time via merchant_emails lookup.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmailEvent(Base):
    __tablename__ = "email_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")

    # Resend event fields
    resend_email_id = Column(String(128), nullable=False)
    event_type = Column(String(32), nullable=False)  # delivered | opened | clicked | bounced | complained
    to_email = Column(String, nullable=True)

    # Resolved context
    shop_domain = Column(String, nullable=True)  # NULL if we can't resolve
    email_type = Column(String(64), nullable=True)  # from merchant_emails lookup

    # Resend timestamp (when the event actually occurred)
    event_timestamp = Column(DateTime, nullable=True)

    # Dedup
    resend_event_id = Column(String(128), nullable=True, unique=True)

    # Raw payload for debugging
    raw_payload = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_email_events_resend_id", "resend_email_id"),
        Index("ix_email_events_shop", "shop_domain"),
        Index("ix_email_events_type", "event_type"),
        Index("ix_email_events_created", "created_at"),
    )
