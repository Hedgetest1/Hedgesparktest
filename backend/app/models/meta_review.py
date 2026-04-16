"""
MetaReview — system-level strategic prioritization.

One row per review_window (weekly). Stores structured JSON output from the
meta-reviewer that ranks proposals, detects conflicts, provides focus guidance.

review_json schema:
{
  "review_window_start": "2026-03-24",
  "review_window_end": "2026-03-30",
  "weekly_focus_area": "reliability",
  "priorities": [
    {"proposal_id": 42, "proposal_type": "...", "reason_snippet": "...", "priority_score": 85, "recommendation": "convert_next"},
    ...
  ],
  "conflicts": [
    {"proposal_ids": [42, 45], "reason": "both touch app/services/nudge_engine.py"},
    ...
  ],
  "deprioritized_classes": [
    {"proposal_type": "refactor", "reason": "0% effectiveness in last 90 days"}
  ],
  "budget_guidance": "LLM budget at 60% — safe to proceed with 2 conversions this week",
  "summary": "Focus on reliability: 5 merchant bug reports in tracker area..."
}
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MetaReview(Base):
    __tablename__ = "meta_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")
    review_window = Column(String(32), nullable=False)  # e.g. "2026-W13"
    status = Column(String(16), nullable=False)                       # completed | skipped
    skipped_reason = Column(String(256), nullable=True)
    review_json = Column(Text, nullable=True)                         # structured JSON output
    proposals_evaluated = Column(Integer, nullable=True)
    model_used = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_meta_reviews_window", "review_window", unique=True),
    )
