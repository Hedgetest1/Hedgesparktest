"""
Immutable action audit log — records every agent/system/admin action.

Append-only by convention: no UPDATE or DELETE operations should ever
be performed on this table.  All code paths must use the
write_audit_log() helper in app/services/audit.py.

Fields are designed for future AI agent traceability:
  - actor_type: "system" | "worker" | "agent" | "merchant" | "admin"
  - action_type: what happened (e.g. "gdpr_delete", "klaviyo_push", "signal_detect")
  - target_type: what was affected (e.g. "merchant", "visitor", "signal")
  - approval_mode: "autonomous" | "human_approved" | None
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc)

    # Who performed the action
    actor_type = Column(String(32), nullable=False)   # system | worker | agent | merchant | admin
    actor_name = Column(String(128), nullable=False)  # e.g. "gdpr_worker", "klaviyo_push", "merchant:shop.myshopify.com"

    # What happened
    action_type = Column(String(64), nullable=False)  # e.g. "gdpr_customer_redact", "klaviyo_event_push"

    # What was affected
    target_type = Column(String(64), nullable=True)   # e.g. "visitor", "merchant", "signal"
    target_id = Column(String(256), nullable=True)    # e.g. visitor_id, shop_domain, signal_id

    # Tenant scope (nullable for cross-tenant system actions)
    shop_domain = Column(String, nullable=True)

    # State snapshots (nullable — not all actions have meaningful state)
    before_state = Column(Text, nullable=True)   # JSON
    after_state = Column(Text, nullable=True)    # JSON

    # Outcome
    status = Column(String(32), nullable=False, default="completed")  # completed | failed | skipped

    # Governance
    approval_mode = Column(String(32), nullable=True)  # autonomous | human_approved | None

    # Extensible metadata
    metadata_json = Column(Text, nullable=True)  # JSON

    __table_args__ = (
        Index("ix_audit_log_shop_created", "shop_domain", "created_at"),
        Index("ix_audit_log_action_type", "action_type", "created_at"),
        Index("ix_audit_log_actor", "actor_name", "created_at"),
    )
