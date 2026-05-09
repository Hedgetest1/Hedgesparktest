"""
SentryIncident — durable record of parsed Sentry error alerts.

Each row represents one parsed error from a Sentry email or webhook.
The table serves as:
  1. Audit trail — raw email preserved for debugging
  2. Dedup registry — message_id and fingerprint prevent duplicates
  3. Triage queue — status tracks processing state
  4. AI input — triage_packet contains structured data for LLM consumption

Statuses:
    received    — raw email stored, not yet parsed
    parsed      — successfully extracted fields from email
    parse_error — parsing failed (raw preserved for manual inspection)
    triaged     — triage packet generated, ready for AI/operator review
    linked      — connected to a bugfix_candidate or ops_alert
    resolved    — no further action needed
    ignored     — operator manually dismissed

Fingerprint:
    SHA-256 of normalized (error_type + culprit + top_frame).
    Used for fuzzy grouping — incidents with the same fingerprint are
    the same underlying error, regardless of cosmetic text differences.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SentryIncident(Base):
    __tablename__ = "sentry_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    # --- Source tracking ---
    # Resend message ID or email Message-ID header — exact dedup key
    source_message_id = Column(String(256), nullable=True, unique=True)
    source_type = Column(String(32), nullable=False, default="email", server_default="email")  # email | webhook | manual

    # --- Raw preservation ---
    raw_subject = Column(String(512), nullable=True)
    raw_body = Column(Text, nullable=True)  # full email body for audit/reparse
    raw_from = Column(String(256), nullable=True)
    raw_to = Column(String(256), nullable=True)

    # --- Parsed fields ---
    error_type = Column(String(256), nullable=True)    # e.g. NameError, TypeError
    error_title = Column(String(512), nullable=True)   # issue title from Sentry
    project = Column(String(128), nullable=True)       # Sentry project name
    environment = Column(String(64), nullable=True)    # production, staging, etc.
    severity = Column(String(32), nullable=True)       # critical, warning, info
    culprit = Column(String(512), nullable=True)       # module/file path
    stack_trace = Column(Text, nullable=True)          # extracted stack trace
    sentry_issue_url = Column(String(512), nullable=True)  # link to Sentry UI

    # --- Normalization ---
    # SHA-256 fingerprint for fuzzy grouping
    fingerprint = Column(String(64), nullable=True)
    # Human-readable normalized key (error_type:culprit:top_frame)
    fingerprint_input = Column(String(512), nullable=True)

    # --- Grouping ---
    # Points to the first incident with the same fingerprint (the "family head")
    family_head_id = Column(Integer, nullable=True)
    recurrence_count = Column(Integer, nullable=False, default=1, server_default="1")

    # --- Triage state ---
    status = Column(String(32), nullable=False, default="received", server_default="received")
    parse_error = Column(String(512), nullable=True)

    # --- AI triage ---
    triage_packet = Column(Text, nullable=True)  # JSON: structured debugging input
    ai_triage_status = Column(String(32), nullable=True)  # pending | ready | consumed | skipped

    # --- Release tracking ---
    release = Column(String(128), nullable=True)  # e.g. "v2.1.3-abc123"
    is_regression_candidate = Column(String(8), nullable=True)  # yes | no | NULL

    # --- Subsystem classification ---
    # frontend_dashboard | backend_api | worker | unknown
    subsystem_class = Column(String(32), nullable=True)

    # --- Merchant impact ---
    # Whether this error directly affects merchant-facing functionality
    merchant_impact = Column(String(16), nullable=True)  # high | medium | low | none
    # shop_domain extracted from Sentry tags/context (if available)
    affected_shop = Column(String(256), nullable=True)

    # --- Integration ---
    # Links to existing system entities when created
    linked_ops_alert_id = Column(Integer, nullable=True)
    lesson_candidate_status = Column(String(32), nullable=True)  # pending | created | skipped

    __table_args__ = (
        Index("ix_sentry_incidents_fingerprint", "fingerprint"),
        Index("ix_sentry_incidents_status", "status"),
        Index("ix_sentry_incidents_created", "created_at"),
        Index("ix_sentry_incidents_family", "family_head_id"),
        Index("ix_sentry_incidents_ai_status", "ai_triage_status"),
    )
