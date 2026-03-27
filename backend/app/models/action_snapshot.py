"""
action_snapshot.py — Baseline + delta metrics for closed-loop proof-of-impact.

Each row captures product metrics at the moment an action is created or a
signal resolves, then computes the delta after 7 days.  This enables the
"Before & After" report that proves whether the merchant's actions worked.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String

from app.core.database import Base


class ActionSnapshot(Base):
    __tablename__ = "action_snapshots"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)
    action_type = Column(String, nullable=False)
    action_task_id = Column(Integer, nullable=True)

    # Baseline metrics at snapshot time
    baseline_cvr = Column(Float, nullable=True)
    baseline_atc_rate = Column(Float, nullable=True)
    baseline_revenue_7d = Column(Float, nullable=True)
    baseline_visitors_7d = Column(Integer, nullable=True)
    baseline_orders_7d = Column(Integer, nullable=True)

    # Signal context
    signal_type = Column(String, nullable=True)
    signal_strength = Column(Float, nullable=True)

    # Lifecycle
    snapshot_at = Column(DateTime, nullable=False)
    compare_after = Column(DateTime, nullable=False)
    delta_computed = Column(Boolean, nullable=False, default=False)

    # Delta results
    delta_cvr = Column(Float, nullable=True)
    delta_atc_rate = Column(Float, nullable=True)
    delta_revenue_7d = Column(Float, nullable=True)
    delta_visitors_7d = Column(Integer, nullable=True)
    delta_orders_7d = Column(Integer, nullable=True)
    delta_computed_at = Column(DateTime, nullable=True)

    outcome = Column(String, nullable=True)  # improved | declined | stable
    summary = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_snapshots_shop_product", "shop_domain", "product_url"),
        Index("ix_snapshots_compare", "delta_computed", "compare_after"),
    )
