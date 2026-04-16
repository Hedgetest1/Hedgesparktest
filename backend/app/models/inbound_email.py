"""
InboundEmail — stored inbound merchant replies.

Populated by POST /webhooks/resend/merchant-inbound when merchants
reply to dev@hedgesparkhq.com or hello@hedgesparkhq.com.

Classification (Phase 1): keyword-based deterministic rules.
Classification (Phase 2): LLM-assisted with confidence scoring.

routing_status tracks the processing pipeline:
    pending    → just received, not yet classified
    classified → intent determined, awaiting routing
    routed     → dispatched to the correct pipeline
    escalated  → flagged for human review
    archived   → noise / no action needed
    responded  → auto-response sent (Phase 2)
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InboundEmail(Base):
    __tablename__ = "inbound_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")

    # Dedup
    message_id = Column(String(256), nullable=True, unique=True)

    # Email fields
    from_email = Column(String, nullable=False)
    to_email = Column(String, nullable=True)
    subject = Column(String(512), nullable=True)
    body_text = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    # Resolved merchant
    shop_domain = Column(String, nullable=True)

    # Classification
    classification = Column(String(32), nullable=True)
    # bug_report | onboarding_confusion | feature_request | suggestion
    # praise | complaint | billing_or_legal | noise
    classification_confidence = Column(String(16), nullable=True)  # high | medium | low
    classification_method = Column(String(16), nullable=True)  # keyword | llm

    # Routing
    routing_action = Column(String(64), nullable=True)
    routing_status = Column(String(16), nullable=False, default="pending", server_default="pending")
    # pending | classified | routed | escalated | archived | responded
    routed_at = Column(DateTime, nullable=True)

    # Agent response (Phase 2)
    agent_response_draft = Column(Text, nullable=True)
    agent_response_sent_at = Column(DateTime, nullable=True)

    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_inbound_emails_shop", "shop_domain"),
        Index("ix_inbound_emails_classification", "classification"),
        Index("ix_inbound_emails_routing_status", "routing_status"),
        Index("ix_inbound_emails_created", "created_at"),
        Index("ix_inbound_emails_from", "from_email"),
    )
