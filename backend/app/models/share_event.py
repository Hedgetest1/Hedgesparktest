"""
share_event.py — Tracking model for the viral share loop.

Tracks every share action, click, and downstream install.
Enables viral coefficient measurement and attribution.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class PublicProofShare(Base):
    """A shareable proof report with unique token."""
    __tablename__ = "public_proof_shares"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)
    share_token = Column(String(64), nullable=False, unique=True, index=True)

    # What's being shared
    proof_type = Column(String(32), nullable=False)  # "nudge_lift", "store_proof"
    nudge_id = Column(Integer, nullable=True)

    # Snapshot of proof data at share time (immutable)
    proof_snapshot = Column(JSONB, nullable=False)

    # Pre-formatted share content
    headline = Column(String(256), nullable=False)       # "+63% conversion lift"
    twitter_text = Column(String(512), nullable=True)
    generic_text = Column(String(512), nullable=True)

    # Tracking
    view_count = Column(Integer, nullable=False, default=0)
    click_cta_count = Column(Integer, nullable=False, default=0)
    installs_attributed = Column(Integer, nullable=False, default=0)

    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class ShareEvent(Base):
    """Individual share/click/install events for viral tracking."""
    __tablename__ = "share_events"

    id = Column(Integer, primary_key=True)
    share_token = Column(String(64), nullable=False, index=True)
    event_type = Column(String(16), nullable=False)  # "share", "view", "click_cta", "install"
    channel = Column(String(32), nullable=True)       # "twitter", "copy", "direct"
    referrer = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
