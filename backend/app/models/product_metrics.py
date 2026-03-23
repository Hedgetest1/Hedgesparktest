from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, UniqueConstraint

from app.core.database import Base


class ProductMetrics(Base):
    """
    Pre-aggregated per-(shop_domain, product_url) behavioral metrics.

    Written exclusively by the aggregation worker on an incremental
    watermark-based cycle (every 5 minutes).  Read by the signal
    detection engine instead of the raw events table, eliminating
    full-table scans from the detection hot path.

    All time-windowed counters (views_1h, views_24h, etc.) are
    replaced on each upsert — they represent the current rolling
    window at the time the worker last ran, not a cumulative total.
    """

    __tablename__ = "product_metrics"

    id = Column(Integer, primary_key=True)

    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)

    # Rolling view counts
    views_1h = Column(Integer, nullable=False, default=0)
    views_24h = Column(Integer, nullable=False, default=0)
    views_7d = Column(Integer, nullable=False, default=0)

    # Unique visitor counts
    unique_visitors_24h = Column(Integer, nullable=False, default=0)
    unique_visitors_7d = Column(Integer, nullable=False, default=0)

    # Conversion signals
    cart_conversions_24h = Column(Integer, nullable=False, default=0)

    # Return visitor signal — distinct visitors who viewed this product
    # on 2+ distinct calendar days within the last 7 days.
    return_visitor_count_7d = Column(Integer, nullable=False, default=0)

    # Engagement averages (24h window, NULL if no dwell events yet)
    avg_dwell_24h = Column(Float, nullable=True)
    avg_scroll_24h = Column(Float, nullable=True)

    # Epoch milliseconds of the most recent event for this product.
    # Used by the signal detection engine to skip stale products.
    last_event_at = Column(BigInteger, nullable=True)

    # When this row was last written by the aggregation worker.
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_product_metrics_shop_product",
        ),
    )
