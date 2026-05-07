"""
MerchantJourneyState — per-merchant email journey state machine.

Tracks every significant touchpoint in the merchant lifecycle:
    - beta invite sent / opened / clicked
    - onboarding started / completed
    - 48h follow-up sent (+ variant used)
    - activation emails sent
    - inbound reply received

current_stage is a derived summary field updated on every state transition.
All timestamps are UTC, timezone-naive (matches project convention).

Used by:
    - email_journey.py (state transitions)
    - followup worker (eligibility queries)
    - Resend event webhook (opened/clicked updates)
    - Operator visibility (GET /ops/journey)
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MerchantJourneyState(Base):
    __tablename__ = "merchant_journey_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_domain = Column(String, nullable=False, unique=True)

    # --- Invite stage ---
    beta_invite_sent_at = Column(DateTime, nullable=True)
    beta_invite_resend_id = Column(String(128), nullable=True)
    beta_invite_opened_at = Column(DateTime, nullable=True)
    beta_invite_clicked_at = Column(DateTime, nullable=True)

    # --- Onboarding stage ---
    onboarding_started_at = Column(DateTime, nullable=True)
    onboarding_completed_at = Column(DateTime, nullable=True)

    # --- 48h follow-up ---
    followup_48h_sent_at = Column(DateTime, nullable=True)
    followup_48h_variant = Column(String(64), nullable=True)
    followup_48h_resend_id = Column(String(128), nullable=True)
    followup_48h_opened_at = Column(DateTime, nullable=True)
    followup_48h_clicked_at = Column(DateTime, nullable=True)

    # --- Activation emails ---
    lite_activation_sent_at = Column(DateTime, nullable=True)
    pro_activation_sent_at = Column(DateTime, nullable=True)

    # --- Inbound ---
    inbound_reply_received_at = Column(DateTime, nullable=True)

    # --- Email health ---
    # Set when a hard bounce or complaint is received. Blocks future sends.
    # Value: "bounced" | "complained" | NULL (healthy)
    email_suppressed = Column(String(32), nullable=True)
    email_suppressed_at = Column(DateTime, nullable=True)

    # --- Derived state ---
    # Possible values:
    #   new, invited, opened, clicked, onboarding, active,
    #   followed_up, activated_lite, activated_pro, replied
    current_stage = Column(String(32), nullable=False, default="new", server_default="new")

    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))
    updated_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"), onupdate=_now_utc)

    __table_args__ = (
        Index("ix_journey_shop", "shop_domain"),
        Index("ix_journey_stage", "current_stage"),
        Index("ix_journey_invite_sent", "beta_invite_sent_at"),
    )
