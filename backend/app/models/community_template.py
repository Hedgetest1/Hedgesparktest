"""
community_template.py — Phase Ω''' community marketplace templates.

Merchants share nudge variants and rule presets with the community.
Each template carries provenance (author shop), an upvote counter for
social proof, and a clone counter for adoption tracking.

Two tables:
  * community_templates   — published templates (nudge | rule)
  * community_template_clones  — per-shop adoption log (one row per clone)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class CommunityTemplate(Base):
    __tablename__ = "community_templates"

    id = Column(Integer, primary_key=True)
    template_type = Column(String(16), nullable=False)  # "nudge" | "rule"
    title = Column(String(200), nullable=False)
    description = Column(String(500), nullable=True)

    author_shop = Column(String, nullable=False, index=True)
    author_label = Column(String(120), nullable=True)  # display name (optional)

    # Vertical scoping — surfaced first to merchants in the same vertical.
    vertical = Column(String(32), nullable=False, default="other", server_default="other")

    # The actual template payload — JSONB for flexibility. The shape
    # depends on template_type:
    #   nudge: {"nudge_type": "scarcity", "copy": "...", "trigger": "...", ...}
    #   rule:  {"trigger_signal": "...", "conditions": [...], "action": {...}}
    payload = Column(JSONB, nullable=False)

    # Engagement stats
    upvotes = Column(Integer, nullable=False, default=0, server_default="0")
    clone_count = Column(Integer, nullable=False, default=0, server_default="0")

    status = Column(String(16), nullable=False, default="published", server_default="published")  # published|hidden|removed

    created_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()", onupdate=utc_now_naive)

    __table_args__ = (
        Index("ix_community_templates_type_vertical", "template_type", "vertical"),
        Index("ix_community_templates_status_clones", "status", "clone_count"),
    )


class CommunityTemplateClone(Base):
    """One row per (template, cloning shop). Used to dedupe and rank."""
    __tablename__ = "community_template_clones"

    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey("community_templates.id"), nullable=False, index=True)
    shop_domain = Column(String, nullable=False, index=True)
    cloned_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")

    __table_args__ = (
        UniqueConstraint("template_id", "shop_domain", name="uq_community_clones_template_shop"),
    )
