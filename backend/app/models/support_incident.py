"""
SupportIncident — Structured record for merchant support issues.

Every non-trivial merchant chatbot interaction creates an incident.
Incidents link to the autonomous pipeline (bugfix, ops alerts, evolution).
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SupportIncident(Base):
    __tablename__ = "support_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")

    # Merchant context
    shop_domain = Column(String(255), nullable=False, index=True)

    # Source + original input
    source = Column(String(32), nullable=False, default="merchant_chat", server_default="merchant_chat")
    original_message = Column(Text, nullable=False)

    # Classification
    classification = Column(String(64), nullable=False)  # product_question, bug_report, etc.
    severity = Column(String(16), nullable=False, default="low", server_default="low")  # low/medium/high/critical
    confidence = Column(String(16), nullable=True)  # high/medium/low
    affected_area = Column(String(64), nullable=True)  # dashboard, tracker, klaviyo, etc.

    # Status lifecycle
    status = Column(String(32), nullable=False, default="open", server_default="open")
    # open → triaged → investigating → resolved → dismissed

    # Autonomous pipeline links
    linked_bugfix_candidate_id = Column(Integer, nullable=True)
    linked_ops_alert_id = Column(Integer, nullable=True)
    linked_evolution_proposal_id = Column(Integer, nullable=True)

    # Resolution
    resolution_summary = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(128), nullable=True)  # system / operator / auto_repair

    # Response given to merchant
    response_text = Column(Text, nullable=True)

    # Resolution verification — blocks premature "fixed" messages
    # NULL = not verified, True = confirmed working, False = fix failed
    resolution_verified = Column(Boolean, nullable=True)

    # Outcome linkage — connects to bugfix effectiveness measurement
    # effective | ineffective | inconclusive | NULL
    fix_outcome = Column(String(32), nullable=True)

    # Resolution delivery tracking — NULL = not yet delivered to merchant chat
    resolution_delivered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_support_incidents_status_created", "status", "created_at"),
    )
