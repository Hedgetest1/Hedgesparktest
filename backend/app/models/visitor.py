from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class Visitor(Base):
    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, index=True)

    visitor_id = Column(String, nullable=False)

    email = Column(String, nullable=True)

    first_seen = Column(DateTime, default=utc_now_naive)

    last_seen = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    shop_domain = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("visitor_id", "shop_domain", name="uq_visitor_shop"),
    )
