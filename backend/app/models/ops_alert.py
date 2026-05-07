"""
Operational alert — durable internal alert record for operator/agent visibility.

Alerts are written by system components when operationally significant events
occur. They are NOT notifications — no external delivery (Slack, email) exists
yet. They are a queryable, structured, append-only record that future alerting
channels or AI agents can read.

Severity levels:
    critical — requires immediate human attention
    warning  — degraded state, auto-remediation attempted or possible
    info     — notable event, no action needed
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OpsAlert(Base):
    __tablename__ = "ops_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    severity = Column(String(16), nullable=False)      # critical | warning | info
    source = Column(String(64), nullable=False)         # component that raised it
    alert_type = Column(String(64), nullable=False)     # e.g. webhook_drift, gdpr_failure
    shop_domain = Column(String, nullable=True)         # tenant scope if applicable
    summary = Column(String(512), nullable=False)       # human/agent-readable summary
    detail = Column(Text, nullable=True)                # JSON or text with full context
    resolved = Column(Boolean, nullable=False, default=False, server_default="false")
    resolved_at = Column(DateTime, nullable=True)

    # External delivery tracking (Slack, etc.)
    delivered_at = Column(DateTime, nullable=True)
    delivery_status = Column(String(16), nullable=True)    # sent | failed | skipped
    delivery_error = Column(String(256), nullable=True)

    __table_args__ = (
        Index("ix_ops_alerts_severity_created", "severity", "created_at"),
        Index("ix_ops_alerts_unresolved", "resolved", "created_at"),
        Index("ix_ops_alerts_source_type", "source", "alert_type", "created_at"),
    )
