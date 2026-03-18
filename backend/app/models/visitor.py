from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime

from app.core.database import Base


class Visitor(Base):
    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, index=True)

    visitor_id = Column(String, nullable=False, index=True)

    email = Column(String, nullable=True)

    first_seen = Column(DateTime, default=datetime.utcnow)

    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shop_domain = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("visitor_id", "shop_domain", name="uq_visitor_shop"),
    )
