"""
ScalingRecommendation — Structured scaling recommendation for human review.

Created by the scaling intelligence engine when trend analysis
indicates infrastructure or capacity changes may be needed.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, Numeric, String, Text, text

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ScalingRecommendation(Base):
    __tablename__ = "scaling_recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default=text("now()"))

    resource_type = Column(String(64), nullable=False)  # vps, redis, llm_budget, email, database
    title = Column(String(255), nullable=False)
    reason = Column(Text, nullable=False)

    current_value = Column(String(128), nullable=True)
    projected_value = Column(String(128), nullable=True)
    projected_horizon_days = Column(Integer, nullable=True, default=30)

    severity = Column(String(16), nullable=False, default="info", server_default="info")  # info / warning / critical
    confidence = Column(String(16), nullable=False, default="low", server_default="low")  # low / medium / high

    estimated_cost_increase_eur = Column(Numeric(18, 2), nullable=True)

    # Lifecycle
    status = Column(String(32), nullable=False, default="active", server_default="active")  # active / acknowledged / dismissed
    acknowledged_by = Column(String(128), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    # Dedup
    dedup_key = Column(String(128), nullable=True, unique=True)

    __table_args__ = (
        Index("ix_scaling_rec_status_created", "status", "created_at"),
    )
