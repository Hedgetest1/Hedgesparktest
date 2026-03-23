from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

from app.core.database import Base


class PriceIntelligence(Base):
    __tablename__ = "price_intelligence"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)

    market_status = Column(String)
    price_position = Column(String)
    price_opportunity = Column(String)
    recommended_price_action = Column(String)
    intelligence_explanation = Column(String)

    confidence_score = Column(Integer, default=0)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_price_intelligence_shop_product",
        ),
        Index("ix_price_intelligence_shop_domain", "shop_domain"),
    )
