"""
ModelUpgradeProposal — tracks model version upgrade candidates and evaluations.

status: pending | evaluating | evaluated | blocked | approved | rejected | activated | expired
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ModelUpgradeProposal(Base):
    __tablename__ = "model_upgrade_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    # Current vs candidate
    current_provider = Column(String(32), nullable=False)
    current_model = Column(String(128), nullable=False)
    candidate_provider = Column(String(32), nullable=False)
    candidate_model = Column(String(128), nullable=False)
    target_module = Column(String(64), nullable=False)   # orchestrator | bugfix_proposal | evolution_audit

    # Proposal
    reason = Column(Text, nullable=True)
    expected_benefit = Column(Text, nullable=True)
    risk_level = Column(String(16), nullable=False, default="LEVEL_2", server_default="LEVEL_2")

    # Evaluation
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    eval_result = Column(String(16), nullable=True)      # pass | inconclusive | fail
    eval_detail = Column(Text, nullable=True)             # JSON
    eval_at = Column(DateTime, nullable=True)

    # Decision
    decided_by = Column(String(128), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_model_upgrade_status", "status", "created_at"),
        Index("ix_model_upgrade_dedup", "current_model", "candidate_model", "target_module"),
    )
