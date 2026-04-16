from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class WorkerLog(Base):
    """
    Append-only execution log for background worker cycles.

    One row is written per worker cycle (success or failure).
    Provides structured observability without requiring log file
    access — queryable by the dashboard, health checks, and future
    AI system agents.

    finished_at and duration_ms are NULL if the worker crashed
    before completing the cycle.  error_detail captures the last
    exception message when errors > 0.
    """

    __tablename__ = "worker_log"

    id = Column(Integer, primary_key=True)

    worker_name = Column(String, nullable=False)

    started_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")
    finished_at = Column(DateTime, nullable=True)

    # Counts for the completed cycle.
    shops_processed = Column(Integer, nullable=False, default=0, server_default="0")
    rows_written = Column(Integer, nullable=False, default=0, server_default="0")
    errors = Column(Integer, nullable=False, default=0, server_default="0")

    # Last exception message, populated when errors > 0.
    error_detail = Column(String, nullable=True)

    # Wall-clock duration of the cycle in milliseconds.
    duration_ms = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_worker_log_worker_name", "worker_name"),
        Index("ix_worker_log_started_at", "started_at"),
        Index("ix_worker_log_name_started", "worker_name", text("started_at DESC")),
    )
