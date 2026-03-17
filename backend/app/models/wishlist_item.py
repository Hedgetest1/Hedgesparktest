from sqlalchemy import Column, Integer, ForeignKey, DateTime
from datetime import datetime

from app.core.database import Base


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id = Column(Integer, primary_key=True)

    visitor_id = Column(Integer, ForeignKey("visitors.id"))

    product_id = Column(Integer, ForeignKey("products.id"))

    created_at = Column(DateTime, default=datetime.utcnow)
