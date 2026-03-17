from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime

from app.core.database import Base


class ProductOpportunity(Base):
    __tablename__ = "product_opportunities"

    id = Column(Integer, primary_key=True)

    product_url = Column(String, unique=True, nullable=False)

    records = Column(Integer, default=0)
    avg_intent_score = Column(Float, default=0)
    hot_count = Column(Integer, default=0)
    wishlist_count = Column(Integer, default=0)
    avg_dwell_seconds = Column(Float, default=0)
    avg_scroll_depth = Column(Float, default=0)

    opportunity_type = Column(String)
    priority_score = Column(Integer, default=0)
    recommended_action = Column(String)
    opportunity_explanation = Column(String)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)
