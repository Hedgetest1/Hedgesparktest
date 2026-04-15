"""
execution.py — Execution tracking, causal measurement, and holdout models.

Five tables forming the execution measurement system:

  ExecutionOpportunity  — persistent opportunity with execution lifecycle + holdout config
  ExecutionAudience     — visitor membership with group assignment (exposed/holdout)
  ExecutionTracking     — outcome measurement per visitor with group assignment
  ExecutionBaseline     — pre-execution snapshot for causal comparison

Holdout design:
  Each audience member is deterministically assigned to exposed or holdout
  using hash(visitor_id + execution_id) % 100 < holdout_pct.
  Same visitor always gets same group for same opportunity.
  Outcomes are tracked for BOTH groups; lift = exposed - holdout.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, Numeric, String, Text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class ExecutionOpportunity(Base):
    """
    One row per detected opportunity (shop_domain x execution_id).
    Includes execution lifecycle, holdout config, and precomputed lift metrics.
    """
    __tablename__ = "execution_opportunities"

    id = Column(Integer, primary_key=True)
    execution_id = Column(String(12), nullable=False)
    shop_domain = Column(String, nullable=False)
    opp_type = Column(String(16), nullable=False)
    product_a = Column(String, nullable=False)
    product_b = Column(String, nullable=False)
    audience_size = Column(Integer, nullable=False, default=0)
    suggested_message = Column(Text, nullable=True)
    timing = Column(String(128), nullable=True)
    expected_impact = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    refreshed_at = Column(DateTime, nullable=False, default=utc_now_naive)

    # Execution lifecycle
    execution_status = Column(String(16), nullable=False, default="suggested")
    executed_at = Column(DateTime, nullable=True)
    execution_mode = Column(String(16), nullable=True)
    execution_note = Column(String(500), nullable=True)

    # Holdout configuration
    holdout_pct = Column(Integer, nullable=False, default=20)
    enforcement_mode = Column(String(16), nullable=False, default="unknown")  # email | onsite | unknown

    # Before/after measurement (precomputed by worker)
    post_return_rate = Column(Float, nullable=True)
    post_view_rate = Column(Float, nullable=True)
    post_purchase_rate = Column(Float, nullable=True)
    post_sample_size = Column(Integer, nullable=False, default=0)
    delta_return_rate = Column(Float, nullable=True)
    delta_view_rate = Column(Float, nullable=True)
    delta_purchase_rate = Column(Float, nullable=True)

    # Exposed vs holdout (counterfactual, precomputed by worker)
    exposed_sample_size = Column(Integer, nullable=False, default=0)
    holdout_sample_size = Column(Integer, nullable=False, default=0)
    return_rate_exposed = Column(Float, nullable=True)
    view_rate_exposed = Column(Float, nullable=True)
    purchase_rate_exposed = Column(Float, nullable=True)
    return_rate_holdout = Column(Float, nullable=True)
    view_rate_holdout = Column(Float, nullable=True)
    purchase_rate_holdout = Column(Float, nullable=True)
    lift_return_rate = Column(Float, nullable=True)
    lift_view_rate = Column(Float, nullable=True)
    lift_purchase_rate = Column(Float, nullable=True)

    # Confidence (updated by worker based on holdout comparison)
    confidence_label = Column(String(16), nullable=True)

    __table_args__ = (
        Index("uq_exec_opp_id", "shop_domain", "execution_id", unique=True),
        Index("ix_exec_opp_shop", "shop_domain"),
        Index("ix_exec_opp_status", "shop_domain", "execution_status"),
    )


class ExecutionAudience(Base):
    """Visitor membership with deterministic group assignment."""
    __tablename__ = "execution_audiences"

    id = Column(Integer, primary_key=True)
    execution_id = Column(String(12), nullable=False)
    shop_domain = Column(String, nullable=False)
    visitor_id = Column(String, nullable=False)
    group_type = Column(String(8), nullable=False, default="exposed")  # exposed | holdout
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        Index("uq_exec_aud_exec_visitor", "execution_id", "visitor_id", unique=True),
        Index("ix_exec_aud_shop_exec", "shop_domain", "execution_id"),
        Index("ix_exec_aud_visitor", "visitor_id"),
        Index("ix_exec_aud_group", "execution_id", "group_type"),
        Index("ix_exec_aud_shop_visitor", "shop_domain", "visitor_id"),
    )


class ExecutionTracking(Base):
    """Outcome measurement per visitor with group assignment."""
    __tablename__ = "execution_tracking"

    id = Column(Integer, primary_key=True)
    execution_id = Column(String(12), nullable=False)
    shop_domain = Column(String, nullable=False)
    visitor_id = Column(String, nullable=False)
    group_type = Column(String(8), nullable=False, default="exposed")  # exposed | holdout
    exposed_at = Column(DateTime, nullable=False, default=utc_now_naive)
    returned = Column(Boolean, nullable=False, default=False)
    viewed_product_b = Column(Boolean, nullable=False, default=False)
    purchased_product_b = Column(Boolean, nullable=False, default=False)
    leakage_suspected = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        Index("uq_exec_track_exec_visitor", "execution_id", "visitor_id", unique=True),
        Index("ix_exec_track_shop_exec", "shop_domain", "execution_id"),
        Index("ix_exec_track_visitor", "visitor_id"),
        Index("ix_exec_track_group", "execution_id", "group_type"),
    )


class ExecutionBaseline(Base):
    """Pre-execution snapshot captured at execution confirmation time."""
    __tablename__ = "execution_baselines"

    id = Column(Integer, primary_key=True)
    execution_id = Column(String(12), nullable=False)
    shop_domain = Column(String, nullable=False)
    captured_at = Column(DateTime, nullable=False, default=utc_now_naive)
    audience_size = Column(Integer, nullable=False, default=0)
    return_rate = Column(Float, nullable=True)
    view_rate = Column(Float, nullable=True)
    purchase_rate = Column(Float, nullable=True)
    tracked_count = Column(Integer, nullable=False, default=0)
    product_b = Column(String, nullable=True)
    product_b_views_24h = Column(Integer, nullable=True)
    product_b_carts_24h = Column(Integer, nullable=True)
    product_b_purchases_24h = Column(Integer, nullable=True)
    product_b_revenue_24h = Column(Numeric(18, 2), nullable=True)

    __table_args__ = (
        Index("uq_exec_baseline_id", "shop_domain", "execution_id", unique=True),
    )
