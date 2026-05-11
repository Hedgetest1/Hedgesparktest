"""cross_shop_pattern.py — vertical-level aggregate of measured lifts.

Sprint 3 #3 of per-shop learning engine roadmap (2026-05-09). Aggregates
holdout-measured outcomes from brain_decisions across all merchants of
the same vertical, GDPR-clean (no shop_domain stored, k-anonymity ≥3
enforced at SQL level).

Read by store_intelligence_profile + sip_engine to derive a Bayesian
prior for new merchants beyond the static industry baselines wired by
Sprint 2 #4.

Written by app/services/cross_shop_aggregator.py on a 6h cadence
(hooked in aggregation_worker via Redis claim hs:cross_shop_aggregator:
next_run).
"""
from __future__ import annotations

from sqlalchemy import (
    Column, BigInteger, String, DateTime, Float, Integer, Index,
    UniqueConstraint, CheckConstraint, text,
)

from app.core.database import Base


class CrossShopPattern(Base):
    __tablename__ = "cross_shop_patterns"

    id = Column(BigInteger, primary_key=True)

    # Signal address — unique (vertical, action_kind, metric_kind)
    vertical = Column(String(64), nullable=False)
    action_kind = Column(String(64), nullable=False)
    metric_kind = Column(String(64), nullable=False)

    # Aggregate statistic across all shops of this vertical
    lift_pct_avg = Column(Float, nullable=False)
    lift_pct_std = Column(Float, nullable=True)
    p_value = Column(Float, nullable=True)

    # Sample-size context (for downstream confidence weighting)
    n_shops = Column(Integer, nullable=False)  # SQL CHECK >= 3
    n_decisions = Column(Integer, nullable=False)

    # Confidence label derived from n_shops + p_value
    confidence = Column(String(16), nullable=False)
        # 'low' / 'medium' / 'high'

    last_aggregated_at = Column(
        DateTime, nullable=False, server_default=text("now()"),
    )
    created_at = Column(
        DateTime, nullable=False, server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "vertical", "action_kind", "metric_kind",
            name="cross_shop_patterns_unique_signal",
        ),
        CheckConstraint(
            "n_shops >= 3",
            name="cross_shop_patterns_n_shops_min",
        ),
        Index(
            "ix_cross_shop_patterns_vertical_signal",
            "vertical", "action_kind",
        ),
        Index(
            "ix_cross_shop_patterns_vertical_last_agg",
            "vertical", "last_aggregated_at",
        ),
    )
