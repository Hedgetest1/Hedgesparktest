from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Index
from app.core.database import Base


class VisitorProductState(Base):

    __tablename__ = "visitor_product_state"

    id = Column(Integer, primary_key=True)

    visitor_id = Column(Text)
    product_url = Column(Text)

    total_views = Column(Integer, default=0)
    total_dwell_seconds = Column(Integer, default=0)
    max_scroll_depth = Column(Integer, default=0)

    wishlist_added = Column(Boolean, default=False)

    first_seen = Column(DateTime)
    last_seen = Column(DateTime)

    intent_score = Column(Integer, default=0)
    intent_level = Column(Text)

    recommended_action = Column(Text)
    intent_explanation = Column(Text)

    shop_domain = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_vps_shop_product", "shop_domain", "product_url"),
        Index("ix_vps_state_shop_visitor", "shop_domain", "visitor_id"),
    )
