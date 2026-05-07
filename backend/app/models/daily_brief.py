from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Index, Integer, String, Text, UniqueConstraint, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class DailyBrief(Base):
    """
    Persisted daily brief for one (shop_domain, brief_date) pair.

    Written by brief_engine.generate_brief() — either on-demand from
    the /brief/today endpoint (bootstrap path) or by the aggregation
    worker's once-per-calendar-day gate (normal operation, Phase 2
    integration step).

    All columns except summary_text and summary_generated are populated
    deterministically from product_metrics by brief_engine.  summary_text
    is the optional Pro AI narrative; it stays NULL until the worker
    populates it.

    The (shop_domain, brief_date) UNIQUE constraint is the idempotency
    key.  ON CONFLICT DO NOTHING on insert means concurrent requests
    cannot produce duplicate rows.
    """

    __tablename__ = "daily_brief"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)

    # Calendar date this brief covers, e.g. 2026-03-18.
    brief_date = Column(Date, nullable=False)

    # Wall-clock time when this row was written.
    generated_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default=text("now()"))

    # The single merchant-facing headline sentence.  Always populated.
    headline = Column(String, nullable=False)

    # Top opportunity product for this brief date.
    # All four are NULL on the empty-state brief (no product_metrics rows).
    top_product_url = Column(String, nullable=True)
    top_product_label = Column(String, nullable=True)
    top_signal_type = Column(String, nullable=True)
    top_action = Column(String, nullable=True)

    # Count of distinct signal instances detected across all products.
    signals_count = Column(Integer, nullable=False, default=0, server_default="0")

    # Top 3 products serialised as a JSON string.
    # Schema: list of {product_url, product_label, signal_type,
    #                   signal_strength, human_label, human_action}
    # Decoded by the /brief/today endpoint before returning to clients.
    metrics_snapshot = Column(Text, nullable=True)

    # Pro plan: AI-generated narrative paragraph (optional, async).
    # NULL means the worker has not yet run or AI is not configured.
    summary_text = Column(Text, nullable=True)
    summary_generated = Column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "brief_date",
            name="uq_daily_brief_shop_date",
        ),
        Index("ix_daily_brief_shop_domain", "shop_domain"),
    )
