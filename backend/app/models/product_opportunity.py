from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint

from app.core.database import Base


class ProductOpportunity(Base):
    __tablename__ = "product_opportunities"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(Text, nullable=False)

    records = Column(Integer, default=0)
    avg_intent_score = Column(Float, default=0)
    hot_count = Column(Integer, default=0)
    wishlist_count = Column(Integer, default=0)
    avg_dwell_seconds = Column(Float, default=0)
    avg_scroll_depth = Column(Float, default=0)

    opportunity_type = Column(Text)
    priority_score = Column(Integer, default=0)
    recommended_action = Column(Text)
    opportunity_explanation = Column(Text)
    plan_required = Column(Text, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_product_opportunities_shop_product",
        ),
        Index("ix_product_opportunities_shop_domain", "shop_domain"),
    )
