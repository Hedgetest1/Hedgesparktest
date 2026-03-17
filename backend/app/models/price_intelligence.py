from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from app.core.database import Base


class PriceIntelligence(Base):
    __tablename__ = "price_intelligence"

    id = Column(Integer, primary_key=True)

    product_url = Column(String, nullable=False)
    shop_domain = Column(String, nullable=False)

    market_status = Column(String)
    price_position = Column(String)
    price_opportunity = Column(String)
    recommended_price_action = Column(String)
    intelligence_explanation = Column(String)

    confidence_score = Column(Integer, default=0)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)
