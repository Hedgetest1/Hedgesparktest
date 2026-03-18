from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from app.core.database import Base


class OpportunitySignal(Base):
    """
    Persisted output of the rule-based opportunity detection engine.

    One row per (shop_domain, product_url, signal_type) — the unique
    constraint prevents duplicates.  The refreshed_at column is updated
    on every detection run; rows not refreshed within _STALE_HOURS are
    deleted by _persist_signals() to keep the table clean.
    """

    __tablename__ = "opportunity_signals"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False, index=True)
    product_url = Column(String, nullable=False)
    signal_type = Column(String, nullable=False)

    signal_strength = Column(Float, nullable=False, default=0.0)
    explanation = Column(String, nullable=True)

    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    refreshed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            "signal_type",
            name="uq_opportunity_signal_shop_product_type",
        ),
    )
