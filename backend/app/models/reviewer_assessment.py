"""
ReviewerAssessment — structured verdict from the reviewer layer.

Each row records a review of a specific entity (bugfix candidate,
evolution proposal, action approval, model upgrade, etc.).

The reviewer layer produces verdicts that inform — but do not
automatically execute — operator decisions.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Valid verdict values
VERDICTS = {"approve", "approve_with_notes", "refine", "reject"}

# Valid risk levels
RISK_LEVELS = {"low", "medium", "high", "critical"}

# Valid alignment levels
ALIGNMENT_LEVELS = {"strong", "medium", "weak"}

# Valid confidence levels
CONFIDENCE_LEVELS = {"low", "medium", "high"}


class ReviewerAssessment(Base):
    __tablename__ = "reviewer_assessments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    # What was reviewed
    entity_type = Column(String(64), nullable=False)          # bugfix_candidate | evolution_proposal | action_approval | model_upgrade | scaling_recommendation
    entity_id = Column(Integer, nullable=False)

    # Verdict
    verdict = Column(String(32), nullable=False)              # approve | approve_with_notes | refine | reject
    risk_level = Column(String(16), nullable=False)           # low | medium | high | critical
    strategic_alignment = Column(String(16), nullable=False)  # strong | medium | weak
    confidence = Column(String(16), nullable=False)           # low | medium | high
    auto_approvable = Column(Boolean, nullable=False, default=False, server_default="false")

    # Explanation
    summary = Column(Text, nullable=False)
    notes_json = Column(Text, nullable=True)                  # JSON list of strings
    blocking_concerns_json = Column(Text, nullable=True)      # JSON list of strings
    affected_domains_json = Column(Text, nullable=True)       # JSON list of strings

    # Provenance
    reviewer_mode = Column(String(16), nullable=False)        # deterministic | llm_assisted
    # brain_snapshot_id was a soft FK to project_brain_snapshots, deleted
    # in Stage 2-E supersession 2026-05-08. Column kept on the table for
    # historical rows; migration to drop the column is deferred since
    # no live writer references it.
    brain_snapshot_id = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_reviewer_entity", "entity_type", "entity_id"),
        Index("ix_reviewer_verdict", "verdict", "created_at"),
    )
