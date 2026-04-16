"""
Action approval — human-gated execution for TIER_1 orchestrator proposals.

Append-only status transitions: pending → approved | rejected | expired
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActionApproval(Base):
    __tablename__ = "action_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    audit_log_id = Column(Integer, nullable=False, index=True)
    action_type = Column(String(64), nullable=False)
    target_id = Column(String(256), nullable=True)
    shop_domain = Column(String, nullable=True)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")
    expires_at = Column(DateTime, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String(128), nullable=True)
    reason = Column(Text, nullable=True)
    notified_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_action_approvals_status", "status", "created_at"),
    )
