"""
Bug fix candidate — tracks bug triage → patch proposal → human-gated apply.

Status transitions: open → analyzed → patch_proposed → approved | rejected
                                                        → applied | failed
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BugFixCandidate(Base):
    __tablename__ = "bugfix_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc)
    status = Column(String(32), nullable=False, default="open")

    # Source of the bug signal
    source_type = Column(String(32), nullable=False)   # ops_alert | worker_log | outcome | manual
    source_ref = Column(String(256), nullable=True)    # alert_id, worker_name, etc.

    # Bug description
    title = Column(String(256), nullable=False)
    summary = Column(Text, nullable=True)
    context_json = Column(Text, nullable=True)         # JSON: relevant logs/alerts/files

    # Patch proposal (populated by LLM)
    patch_summary = Column(Text, nullable=True)
    patch_diff = Column(Text, nullable=True)           # unified diff or structured patch
    patch_files = Column(Text, nullable=True)          # JSON list of file paths
    test_command = Column(String(512), nullable=True)
    test_result = Column(Text, nullable=True)

    # Decision
    decided_by = Column(String(128), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)

    # Proposal metadata
    proposal_attempted_at = Column(DateTime, nullable=True)
    proposal_error = Column(String(512), nullable=True)
    proposal_provider = Column(String(32), nullable=True)   # anthropic | openai | none

    # Risk classification (0=auto-apply, 1=human-approve, 2=never-auto)
    patch_risk_tier = Column(Integer, nullable=True)

    # Apply metadata
    git_commit_sha = Column(String(64), nullable=True)

    # Slack dedup
    notified_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_bugfix_candidates_status", "status", "created_at"),
        Index("ix_bugfix_candidates_source", "source_type", "source_ref"),
    )
