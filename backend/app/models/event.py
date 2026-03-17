from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)

    visitor_id = Column(String)
    session_id = Column(String, nullable=True)

    event_type = Column(String)

    page_url = Column(String, nullable=True)
    page_title = Column(String, nullable=True)

    source_type = Column(String, nullable=True)
    referrer = Column(String, nullable=True)

    dwell_seconds = Column(Integer, nullable=True)
    scroll_depth = Column(Integer, nullable=True)

    event_data = Column(JSON, nullable=True)

    occurred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    shop_domain = Column(String, nullable=False)
