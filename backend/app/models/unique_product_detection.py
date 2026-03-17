from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime

from app.core.database import Base


class UniqueProductDetection(Base):
    __tablename__ = "unique_product_detection"

    id = Column(Integer, primary_key=True)

    product_url = Column(String, nullable=False)
    shop_domain = Column(String, nullable=False)

    uniqueness_status = Column(String)
    uniqueness_score = Column(Integer, default=0)
    evidence_summary = Column(String)
    recommended_strategy = Column(String)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)
