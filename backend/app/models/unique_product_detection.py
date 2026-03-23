from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint

from app.core.database import Base


class UniqueProductDetection(Base):
    __tablename__ = "unique_product_detection"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)

    uniqueness_status = Column(String)
    uniqueness_score = Column(Integer, default=0)
    evidence_summary = Column(String)
    recommended_strategy = Column(String)
    plan_required = Column(String, default="pro")

    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_unique_product_detection_shop_product",
        ),
        Index("ix_unique_product_detection_shop_domain", "shop_domain"),
    )
