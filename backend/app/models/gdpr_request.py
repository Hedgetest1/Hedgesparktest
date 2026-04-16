"""
GDPR request tracking — persists incoming Shopify GDPR webhooks as
actionable jobs for the gdpr_worker to process.

Request types (from Shopify GDPR mandatory webhooks):
  customers_redact      — delete a specific customer's personal data
  customers_data_request — export a customer's data (log-only in v1)
  shop_redact           — delete ALL data for a shop (48h after uninstall)
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GdprRequest(Base):
    __tablename__ = "gdpr_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # "customers_redact" | "customers_data_request" | "shop_redact"
    request_type = Column(String, nullable=False)

    # Tenant scope — always present
    shop_domain = Column(String, nullable=False)

    # Shopify customer ID — present for customers_redact and customers_data_request.
    # NULL for shop_redact (applies to entire shop).
    customer_id = Column(String, nullable=True)

    # Shopify customer email — present when Shopify includes it.
    # Used for matching shop_orders.customer_email for redaction.
    customer_email = Column(String, nullable=True)

    # "pending" | "processing" | "completed" | "failed"
    status = Column(String, nullable=False, default="pending", server_default="pending")

    # Raw Shopify webhook payload for audit trail
    payload = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")
    processed_at = Column(DateTime, nullable=True)

    # Populated on failure — stores exception message for operator review
    error_detail = Column(Text, nullable=True)

    # Summary of what was deleted/redacted — for audit and compliance reporting
    result_summary = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_gdpr_requests_shop_status", "shop_domain", "status"),
        Index("ix_gdpr_requests_created", "created_at"),
    )
