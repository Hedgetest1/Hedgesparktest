"""Persistent archive for Night Shift Agent reports."""
from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class NightShiftReport(Base):
    __tablename__ = "night_shift_reports"
    __table_args__ = (
        UniqueConstraint("shop_domain", "day", name="uq_night_shift_reports_shop_day"),
        Index("ix_night_shift_reports_shop_created", "shop_domain", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    shop_domain = Column(String(255), nullable=False)
    day = Column(String(10), nullable=False)
    generated_at = Column(DateTime, nullable=False)
    status = Column(String(32), nullable=False, default="quiet", server_default="quiet")
    headline = Column(Text, nullable=True)
    narrative = Column(Text, nullable=True)
    sleep_confidence = Column(Integer, nullable=False, default=0, server_default="0")
    sleep_confidence_label = Column(String(120), nullable=True)
    top_action = Column(JSON, nullable=True)
    journal = Column(JSON, nullable=True)
    metrics = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
