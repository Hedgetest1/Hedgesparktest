from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from app.core.database import Base


class MarketLookup(Base):
    __tablename__ = "market_lookup"

    id = Column(Integer, primary_key=True)

    product_url = Column(String, nullable=False)
    shop_domain = Column(String, nullable=False)

    lookup_status = Column(String)
    comparable_presence = Column(String)
    uniqueness_hint = Column(String)
    lookup_confidence = Column(Integer, default=0)
    market_summary = Column(String)
    recommended_next_step = Column(String)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)
