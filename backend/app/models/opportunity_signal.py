from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, UniqueConstraint

from app.core.database import Base


class OpportunitySignal(Base):
    __tablename__ = "opportunity_signals"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False, index=True)
    product_url = Column(String, nullable=False)
    signal_type = Column(String, nullable=False)

    signal_strength = Column(Float, nullable=False, default=0.0)
    signal_confidence = Column(String(16), nullable=False, default="high", server_default="high")
    explanation = Column(String, nullable=True)

    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    refreshed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # NO Python default → evita TTL = 0
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            "signal_type",
            name="uq_opportunity_signal_shop_product_type",
        ),
        Index("ix_opportunity_signals_expires_at", "expires_at"),
    )
