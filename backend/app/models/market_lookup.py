from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class MarketLookup(Base):
    __tablename__ = "market_lookup"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(Text, nullable=False)

    lookup_status = Column(Text)
    comparable_presence = Column(Text)
    uniqueness_hint = Column(Text)
    lookup_confidence = Column(Integer, default=0)
    market_summary = Column(Text)
    recommended_next_step = Column(Text)
    plan_required = Column(Text, default="pro")

    updated_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_market_lookup_shop_product",
        ),
        Index("ix_market_lookup_shop_domain", "shop_domain"),
    )
