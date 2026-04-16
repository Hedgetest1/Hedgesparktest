"""
Action outcome tracking — measures whether orchestrator actions improved system state.

Links to audit_log rows by audit_log_id. One outcome row per evaluated action.
Append-only by convention (same as audit_log).

outcome_status:
    pending    — action executed, not yet evaluated
    success    — problem resolved after action
    no_effect  — problem persists despite action
    degraded   — system state worsened (rare, detectable for some actions)
    unknown    — evaluation could not determine outcome
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ActionOutcome(Base):
    __tablename__ = "action_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Link to the audit_log entry that recorded the execution
    audit_log_id = Column(Integer, nullable=False, index=True)

    # Denormalized from audit_log for fast querying without joins
    action_type = Column(String(64), nullable=False)
    target_id = Column(String(256), nullable=True)
    shop_domain = Column(String, nullable=True)

    executed_at = Column(DateTime, nullable=False)
    evaluated_at = Column(DateTime, nullable=True)

    # pending | success | no_effect | degraded | unknown
    outcome_status = Column(String(16), nullable=False, default="pending", server_default="pending")
    outcome_detail = Column(Text, nullable=True)  # JSON or descriptive text

    __table_args__ = (
        Index("ix_action_outcomes_status", "outcome_status", "executed_at"),
    )
