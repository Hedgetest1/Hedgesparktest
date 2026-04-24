"""Adversarial review finding — Sprint B of CTO-brain pipeline upgrade.

One row per (BugFixCandidate × lens) — the adversarial_reviewer service
runs 3 LLM calls (internal/investor/competitor CTO persona) against
every TIER_1+ candidate after reviewer_layer passes, and each call's
structured finding lands here.

severity: 0-10 scale
  0-2   no concern, lens approves the fix
  3-5   advisory concern, document but don't block
  6-8   serious concern, trigger iterative fix loop (Sprint C)
  9-10  critical concern, escalate to human review

status:
  open             just created, awaiting iteration or dismissal
  addressed        Sprint C generated a follow-up patch that resolves it
  acknowledged     operator noted but chose not to iterate
  dismissed        operator marked as false-positive
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text,
)

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AdversarialReviewFinding(Base):
    __tablename__ = "adversarial_review_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime, nullable=False, default=_now_utc, server_default="now()",
    )

    bugfix_candidate_id = Column(
        Integer,
        ForeignKey(
            "bugfix_candidates.id",
            ondelete="CASCADE",
            name="fk_adv_review_findings_candidate",
        ),
        nullable=False,
    )

    # Which lens produced this finding.
    # internal        — our own CTO perspective (architecture / scale)
    # investor        — investor-CTO perspective (risk / unit economics)
    # competitor      — competitor CTO+CEO perspective (does it solve
    #                   the class or just the instance?)
    lens = Column(String(32), nullable=False)

    severity = Column(Integer, nullable=False)       # 0-10
    concern = Column(Text, nullable=True)
    suggested_remediation = Column(Text, nullable=True)

    status = Column(String(32), nullable=False, default="open",
                    server_default="open")

    # LLM provenance
    llm_provider = Column(String(32), nullable=True)
    llm_model = Column(String(64), nullable=True)
    tokens_used = Column(Integer, nullable=True)

    # Sprint C — iterative fix loop will link the follow-up candidate here
    addressed_by_candidate_id = Column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "ix_adv_review_findings_candidate",
            "bugfix_candidate_id", "lens",
        ),
        Index(
            "ix_adv_review_findings_status",
            "status", "severity",
        ),
    )
