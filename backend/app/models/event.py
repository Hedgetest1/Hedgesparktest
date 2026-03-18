from sqlalchemy import BigInteger, Column, Integer, String
from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    visitor_id = Column(String)
    event_type = Column(String)
    url = Column(String, nullable=True)        # page URL or product URL
    timestamp = Column(BigInteger, nullable=True)  # epoch milliseconds
    dwell_seconds = Column(Integer, nullable=True)
    max_scroll_depth = Column(Integer, nullable=True)
    shop_domain = Column(String, nullable=False)
