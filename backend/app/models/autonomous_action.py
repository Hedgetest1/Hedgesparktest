"""
autonomous_action.py — System of record for autonomous revenue loop decisions.

Tracks every decision the autonomous loop makes: what signal triggered it,
what action was chosen, why, what risk level was assigned, what happened,
and what the system learned from the outcome.

Status lifecycle:
  proposed   → action recommended but not yet deployed (high risk)
  deployed   → nudge created/modified, measurement started
  measuring  → waiting for sufficient data
  completed  → measurement done, outcome recorded, SIP updated
  suppressed → nudge deactivated due to negative/neutral outcome
  rolled_back → emergency rollback (bounce spike, merchant override, etc.)
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class AutonomousAction(Base):
    __tablename__ = "autonomous_actions"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)

    # What triggered this action
    signal_type = Column(String, nullable=False)       # e.g., "HIGH_TRAFFIC_NO_CART"
    product_url = Column(String, nullable=False)
    nudge_id = Column(Integer, nullable=True)          # FK to active_nudges (set on deploy)

    # What action was chosen
    action_type = Column(String, nullable=False)       # "nudge_deploy", "nudge_suppress", "nudge_promote"
    nudge_type = Column(String, nullable=True)         # "social_proof", "high_interest", etc.

    # Risk assessment
    risk_level = Column(String(8), nullable=False)     # "low", "medium", "high"

    # Decision context (auditable)
    decision_reason = Column(Text, nullable=False)
    sip_confidence = Column(String(8), nullable=True)  # SIP confidence at time of decision
    sip_nudge_score = Column(Float, nullable=True)     # SIP nudge_type_score used

    # Execution status
    status = Column(String(16), nullable=False, default="proposed", index=True)
    deployed_at = Column(DateTime, nullable=True)
    holdout_pct = Column(Integer, nullable=True)

    # Measurement outcome
    measurement_start = Column(DateTime, nullable=True)
    measurement_end = Column(DateTime, nullable=True)
    treatment_cvr = Column(Float, nullable=True)
    control_cvr = Column(Float, nullable=True)
    lift_pct = Column(Float, nullable=True)
    p_value = Column(Float, nullable=True)
    visitors_measured = Column(Integer, nullable=True)
    outcome = Column(String(12), nullable=True)        # "positive", "neutral", "negative"

    # Rollback / suppression
    rollback_reason = Column(Text, nullable=True)

    # Bootstrap flag: manually-forced experiments are excluded from SIP learning
    is_bootstrap = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
