"""
MergeOutcome — tracks post-merge health for autofix promotions.

evaluation_status: pending | healthy | regressed | unknown
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MergeOutcome(Base):
    __tablename__ = "merge_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    promotion_id = Column(Integer, nullable=False, index=True)
    bugfix_candidate_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_now_utc)
    merge_commit_sha = Column(String(64), nullable=True)
    evaluation_status = Column(String(16), nullable=False, default="pending")
    evaluated_at = Column(DateTime, nullable=True)
    detail = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_merge_outcomes_status", "evaluation_status", "created_at"),
    )
