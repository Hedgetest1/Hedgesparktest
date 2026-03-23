from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from app.core.database import Base


class WorkerState(Base):
    """
    Persistent state record for each background worker.

    One row per worker_name.  Workers read this on startup to resume
    from their last known position and write it at the end of each
    successful cycle.

    last_watermark is used by the aggregation worker to implement
    incremental processing: it stores the MAX(events.timestamp)
    processed in the previous cycle so the next cycle only reads
    newer events.  NULL means the worker has never run (process all
    existing data on first cycle).

    last_run_at is a human-readable timestamp used by observability
    queries and the future system health agent.
    """

    __tablename__ = "worker_state"

    id = Column(Integer, primary_key=True)

    # Unique identifier matching the worker process name in PM2.
    worker_name = Column(String, nullable=False, unique=True)

    # UTC timestamp of the last completed cycle.
    last_run_at = Column(DateTime, nullable=True)

    # Epoch milliseconds watermark — last event.timestamp processed.
    # Used by aggregation_worker only; NULL for other workers.
    last_watermark = Column(BigInteger, nullable=True)
