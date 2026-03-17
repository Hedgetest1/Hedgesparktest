from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.core.database import Base


class VisitorProductState(Base):

    __tablename__ = "visitor_product_state"

    id = Column(Integer, primary_key=True)

    visitor_id = Column(String)
    product_url = Column(String)

    total_views = Column(Integer, default=0)
    total_dwell_seconds = Column(Integer, default=0)
    max_scroll_depth = Column(Integer, default=0)

    wishlist_added = Column(Boolean, default=False)

    first_seen = Column(DateTime)
    last_seen = Column(DateTime)

    intent_score = Column(Integer, default=0)
    intent_level = Column(String)

    recommended_action = Column(String)
    intent_explanation = Column(String)

    shop_domain = Column(String, nullable=False)
