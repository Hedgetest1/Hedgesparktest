from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime

from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)

    shopify_product_id = Column(String, index=True)

    title = Column(String)

    price = Column(Float)

    currency = Column(String)

    product_url = Column(String)

    image_url = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    shop_domain = Column(String, nullable=False)
