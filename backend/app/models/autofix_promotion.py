"""
AutoFixPromotion — tracks promotion of local auto-fix commits to remote.

Flow: pending → branch_created → ci_pending → ci_passed|ci_failed
      → approved → pushed | rejected | failed
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AutoFixPromotion(Base):
    __tablename__ = "autofix_promotions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc)
    bugfix_candidate_id = Column(Integer, nullable=False, index=True)
    git_commit_sha = Column(String(64), nullable=False)
    branch_name = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    ci_url = Column(String(512), nullable=True)
    ci_result = Column(Text, nullable=True)
    decided_by = Column(String(128), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    pushed_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)
    notified_at = Column(DateTime, nullable=True)

    # PR + merge
    pr_url = Column(String(512), nullable=True)
    pr_number = Column(Integer, nullable=True)
    merged_at = Column(DateTime, nullable=True)
    merge_commit_sha = Column(String(64), nullable=True)

    # Remote CI
    remote_ci_status = Column(String(32), nullable=True)  # queued|in_progress|passed|failed|unknown|unconfigured
    remote_ci_url = Column(String(512), nullable=True)
    remote_ci_checked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_autofix_promotions_status", "status", "created_at"),
    )
