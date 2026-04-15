from sqlalchemy import Column, Integer, String, Float, DateTime, Numeric
from datetime import datetime
from app.core.database import Base
from app.core.time_utils import utc_now_naive

class PriceWatch(Base):
    __tablename__ = "price_watch"

    id = Column(Integer, primary_key=True, index=True)

    product_id = Column(String, index=True)
    product_name = Column(String)

    competitor_url = Column(String)

    last_seen_price = Column(Numeric(18, 2))
    previous_price = Column(Numeric(18, 2))

    price_drop_detected = Column(Integer, default=0)

    last_checked = Column(DateTime, default=utc_now_naive)

    shop_domain = Column(String, nullable=False)
