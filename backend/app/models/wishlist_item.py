from sqlalchemy import Column, Integer, ForeignKey, DateTime, String
from datetime import datetime

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True)

    visitor_id = Column(Integer, ForeignKey("visitors.id"))

    product_id = Column(Integer, ForeignKey("products.id"))

    created_at = Column(DateTime, default=utc_now_naive)

    shop_domain = Column(String, nullable=False)
