from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.core.database import Base


class PriceIntelligence(Base):
    __tablename__ = "price_intelligence"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(Text, nullable=False)

    market_status = Column(Text)
    price_position = Column(Text)
    price_opportunity = Column(Text)
    recommended_price_action = Column(Text)
    intelligence_explanation = Column(Text)

    confidence_score = Column(Integer, default=0)
    plan_required = Column(Text, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_price_intelligence_shop_product",
        ),
        Index("ix_price_intelligence_shop_domain", "shop_domain"),
    )
