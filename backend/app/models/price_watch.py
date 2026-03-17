from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from app.core.database import Base

class PriceWatch(Base):
    __tablename__ = "price_watch"

    id = Column(Integer, primary_key=True, index=True)

    product_id = Column(String, index=True)
    product_name = Column(String)

    competitor_url = Column(String)

    last_seen_price = Column(Float)
    previous_price = Column(Float)

    price_drop_detected = Column(Integer, default=0)

    last_checked = Column(DateTime, default=datetime.utcnow)
