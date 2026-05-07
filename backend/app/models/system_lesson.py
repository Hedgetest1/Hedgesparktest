"""
SystemLesson — persistent institutional memory for the autonomous loop.

Lessons are generated from measured outcomes (effective/ineffective bugfixes)
and record WHAT worked or failed, in WHICH domain, and WHY.

Queried by:
  - propose_patch() — inject relevant lessons into LLM context
  - review_entity() — adjust risk assessment based on domain history
  - meta_reviewer — strategic prioritization context
  - score_subsystem_weakness() — boost domain weakness scores

Quality control:
  - confidence: 0.0–1.0, decays over time if not reinforced
  - evidence_count: how many data points support this lesson
  - status: active | stale | contradicted | retired
  - Lessons with confidence < 0.3 are pruned by GC
  - Contradicting lessons (same domain, opposite finding) flag for review
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemLesson(Base):
    __tablename__ = "system_lessons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    # Domain this lesson applies to (from project_brain classifier)
    # e.g., "webhooks", "tracking", "auth", "billing", "intelligence"
    domain = Column(String(64), nullable=False)

    # Lesson category
    # effective_pattern | ineffective_pattern | domain_insight | regression_warning
    lesson_type = Column(String(32), nullable=False)

    # Human-readable summary of the lesson
    summary = Column(Text, nullable=False)

    # Structured detail (JSON) — what files, what approach, what outcome
    detail_json = Column(Text, nullable=True)

    # Source traceability — which candidate(s) generated this lesson
    source_candidate_id = Column(Integer, nullable=True)
    source_type = Column(String(32), nullable=True)

    # Quality control
    confidence = Column(Float, nullable=False, default=0.7, server_default="0.7")
    evidence_count = Column(Integer, nullable=False, default=1, server_default="1")
    last_reinforced_at = Column(DateTime, nullable=True)

    # Status lifecycle: active → stale → retired
    # Or: active → contradicted (if opposing evidence found)
    status = Column(String(16), nullable=False, default="active", server_default="active")

    # Dedup key — prevents duplicate lessons for same finding
    dedup_key = Column(String(256), nullable=True)

    # Learning isolation: classifies the evidence source that produced this lesson.
    # pre_merchant | internal_test | sandbox | real_merchant
    # Only real_merchant lessons may influence product reasoning (confidence boosts,
    # reinforcement weights, strategic memory). Pre-merchant/test/sandbox lessons
    # remain available for technical hardening (patch formatting, failure taxonomy).
    evidence_source = Column(String(32), nullable=True, default="pre_merchant")

    # Promotion validation — prevents auto-promoted lessons from becoming unchecked dogma
    # NULL = not promoted, pending_promotion = awaiting human review,
    # promoted = confirmed, rejected_promotion = human rejected
    promotion_status = Column(String(32), nullable=True)
    promoted_at = Column(DateTime, nullable=True)
    promotion_decided_by = Column(String(64), nullable=True)  # operator | auto_confirm

    __table_args__ = (
        Index("ix_lessons_domain_status", "domain", "status"),
        Index("ix_lessons_type_status", "lesson_type", "status"),
        Index("ix_lessons_confidence", "confidence", "status"),
        Index("ix_lessons_dedup", "dedup_key"),
        Index("ix_lessons_created", "created_at"),
        Index("ix_lessons_promotion_status", "promotion_status"),
    )
