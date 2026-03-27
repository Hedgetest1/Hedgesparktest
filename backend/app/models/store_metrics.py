"""
store_metrics.py — Precomputed store-level intelligence.

One row per shop_domain. Written exclusively by the aggregation worker.
Read by the store intelligence API (read-only, no runtime computation).

Contains:
  - co_viewed_pairs: top 10 product pairs by shared visitors (JSONB array)
  - cohort snapshot: new vs returning visitor counts and cart rates

Execution opportunities are stored in the separate execution_opportunities
table (not in JSONB) for scalability and proof loop tracking.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class StoreMetrics(Base):
    __tablename__ = "store_metrics"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, unique=True)

    # Co-viewed product pairs (JSONB array, top 10)
    co_viewed_pairs = Column(JSONB, nullable=False, default=list)

    # Cohort snapshot (7d window)
    new_visitors_7d = Column(Integer, nullable=False, default=0)
    returning_visitors_7d = Column(Integer, nullable=False, default=0)
    new_visitor_cart_rate = Column(Float, nullable=True)
    returning_visitor_cart_rate = Column(Float, nullable=True)

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
