from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

from app.core.database import Base


class MarketLookup(Base):
    __tablename__ = "market_lookup"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)

    lookup_status = Column(String)
    comparable_presence = Column(String)
    uniqueness_hint = Column(String)
    lookup_confidence = Column(Integer, default=0)
    market_summary = Column(String)
    recommended_next_step = Column(String)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_market_lookup_shop_product",
        ),
        Index("ix_market_lookup_shop_domain", "shop_domain"),
    )
