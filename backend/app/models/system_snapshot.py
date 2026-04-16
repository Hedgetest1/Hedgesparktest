"""
SystemSnapshot — Daily system metrics snapshot for trend analysis.

One row per day. Captures merchant counts, infra metrics, LLM usage,
and operational health. Used by scaling intelligence for forecasting.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Date, Float, Integer, Numeric, String, Index

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemSnapshot(Base):
    __tablename__ = "system_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, nullable=False, default=_now_utc, server_default="now()")
    date_bucket = Column(Date, nullable=False, unique=True)

    # Merchant counts
    active_merchants = Column(Integer, nullable=False, default=0, server_default="0")
    billing_active_merchants = Column(Integer, nullable=False, default=0, server_default="0")

    # Event volume
    total_events_24h = Column(Integer, nullable=True)

    # LLM
    llm_calls_24h = Column(Integer, nullable=True, default=0)
    llm_estimated_cost_eur = Column(Numeric(18, 2), nullable=True, default=0)

    # Worker health
    worker_error_rate = Column(Float, nullable=True, default=0.0)

    # Infrastructure
    cpu_pct = Column(Float, nullable=True)
    ram_used_mb = Column(Float, nullable=True)
    ram_total_mb = Column(Float, nullable=True)
    disk_used_pct = Column(Float, nullable=True)

    # Operational
    api_warning_count = Column(Integer, nullable=True, default=0)
    support_incident_count = Column(Integer, nullable=True, default=0)
    ops_alert_count = Column(Integer, nullable=True, default=0)
