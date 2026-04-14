"""
Bug fix candidate — tracks bug triage → patch proposal → human-gated apply.

Status transitions: open → analyzed → patch_proposed → approved | rejected
                                                        → applied | failed

Outcome tracking (post-apply):
    outcome_status: pending → effective | ineffective | inconclusive
    Measured 48h after applied_at by checking if the original alert pattern recurred.
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

    # Reviewer link
    reviewer_assessment_id = Column(Integer, nullable=True)

    # Slack dedup
    notified_at = Column(DateTime, nullable=True)

    # Post-apply outcome tracking (closed-loop learning)
    outcome_status = Column(String(32), nullable=True)     # pending | effective | ineffective | inconclusive
    outcome_measured_at = Column(DateTime, nullable=True)
    outcome_evidence = Column(Text, nullable=True)         # JSON: {alerts_before, alerts_after, ...}

    # Domain classification (from project_brain) — enables per-domain effectiveness
    affected_domain = Column(String(64), nullable=True)

    # Priority scoring — deterministic, explainable, drives queue order
    priority_score = Column(Integer, nullable=True)       # 0-100, higher = fix first
    priority_detail = Column(Text, nullable=True)         # JSON: breakdown of score components

    # Fix confidence — how trustworthy is this proposed fix?
    fix_confidence = Column(Integer, nullable=True)       # 0-100, gates auto-apply
    confidence_detail = Column(Text, nullable=True)       # JSON: breakdown of confidence components

    # Remediation class — deterministic classification of the type of fix
    remediation_class = Column(String(32), nullable=True)

    # Lesson effectiveness tracking — JSON list of SystemLesson IDs injected into proposal context
    lesson_ids_used = Column(Text, nullable=True)

    # Learning isolation: classifies the evidence environment.
    # pre_merchant | internal_test | sandbox | real_merchant
    evidence_source = Column(String(32), nullable=True, default="pre_merchant")

    __table_args__ = (
        Index("ix_bugfix_candidates_status", "status", "created_at"),
        Index("ix_bugfix_candidates_source", "source_type", "source_ref"),
        Index("ix_bugfix_candidates_domain", "affected_domain", "outcome_status"),
        Index("ix_bugfix_candidates_outcome", "outcome_status", "outcome_measured_at"),
    )
