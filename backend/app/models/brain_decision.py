"""brain_decision.py — record of MerchantBrain coordination cycles.

Born 2026-05-07 closing the founder direttiva "shippa Brain Vero":
brain must be the conductor of the merchant-outcome loop, not a
self-monitoring immune system. Every brain tick that produces a
decision (action dispatched OR no-op rationale) writes a row here.

Outcome measurement closes the loop: each decision carries an
`expected_outcome_metric` + `outcome_window_hours`. After the window
elapses, an evaluator compares the metric delta and stamps
`outcome_status` ∈ {effective, ineffective, neutral, evaluation_failed}.

Schema is deliberately denormalized — reads are by-merchant time series
+ aggregate effectiveness reports, not transactional. Retention 90d.
"""
from __future__ import annotations

from sqlalchemy import (
    Column, BigInteger, String, DateTime, Float, Integer, Index, text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class BrainDecision(Base):
    __tablename__ = "brain_decisions"

    id = Column(BigInteger, primary_key=True)
    decision_at = Column(
        DateTime, nullable=False, server_default=text("now()"),
    )

    # Tenancy
    shop_domain = Column(String, nullable=False)

    # Synthesizer output — the cross-subsystem narrative this tick saw
    sense_snapshot = Column(JSONB, nullable=True)  # raw signals read
    synthesis = Column(String(2000), nullable=True)  # 1-paragraph narrative

    # Decision
    action_kind = Column(String(64), nullable=False)
        # e.g. "retention_outreach_email" / "proactive_chat_nudge" /
        # "recovery_digest" / "no_action_chosen"
    action_payload = Column(JSONB, nullable=True)
    rationale = Column(String(500), nullable=True)

    # Coordination — what limb was dispatched
    limb_dispatched = Column(String(64), nullable=True)
        # e.g. "email_orchestrator" / "orchestrator" / "nudge_composer"
    limb_response = Column(JSONB, nullable=True)
        # intent_id / action_task_id / etc. so we can correlate downstream

    # Learn — outcome measurement
    expected_outcome_metric = Column(String(64), nullable=True)
        # e.g. "rars_delta" / "cvr_delta_7d" / "merchant_re_engaged"
    outcome_window_hours = Column(Integer, nullable=False, server_default=text("24"))
    baseline_value = Column(Float, nullable=True)  # metric at decision time
    measured_value = Column(Float, nullable=True)  # metric after window
    outcome_status = Column(String(32), nullable=True)
        # null = pending; 'effective'/'ineffective'/'neutral'/'evaluation_failed'
    outcome_evaluated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_brain_decisions_shop_at", "shop_domain", "decision_at"),
        Index("ix_brain_decisions_status_at",
              "outcome_status", "decision_at"),
        Index("ix_brain_decisions_action", "action_kind"),
    )
