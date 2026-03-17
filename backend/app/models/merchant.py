from sqlalchemy import Boolean, Column, DateTime, Integer, String
from datetime import datetime

from app.core.database import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_domain = Column(String, unique=True, nullable=False, index=True)
    access_token = Column(String, nullable=True)
    plan = Column(String, nullable=False, default="starter")
    installed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    billing_active = Column(Boolean, default=False, nullable=False)
