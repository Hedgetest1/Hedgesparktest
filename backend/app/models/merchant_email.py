"""
MerchantEmail — durable log of every lifecycle email sent (or suppressed).

Every email attempt is recorded:
  - sent      → Resend accepted the message
  - failed    → Resend rejected or network error
  - suppressed → dedup/cooldown/missing-email prevented sending

This table is the single source of truth for "what emails did shop X get?"
Used by operators (GET /ops/emails) and by dedup logic.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Index, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MerchantEmail(Base):
    __tablename__ = "merchant_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    shop_domain = Column(String, nullable=False)
    email_type = Column(String(64), nullable=False)

    # Delivery details
    to_email = Column(String, nullable=True)  # NULL when suppressed before send
    subject = Column(String(256), nullable=True)
    status = Column(String(32), nullable=False)  # sent | failed | suppressed

    # Resend tracking
    resend_id = Column(String(128), nullable=True)

    # Why it was suppressed (NULL if actually sent or failed)
    suppressed_by = Column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_merchant_emails_shop", "shop_domain"),
        Index("ix_merchant_emails_type", "email_type"),
        Index("ix_merchant_emails_shop_type", "shop_domain", "email_type"),
        Index("ix_merchant_emails_created", "created_at"),
        Index("ix_merchant_emails_shop_created", "shop_domain", text("created_at DESC")),
    )
