from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Float, Index, Integer, Numeric, String, UniqueConstraint, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


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
    views_1h = Column(Integer, nullable=False, default=0, server_default="0")
    views_24h = Column(Integer, nullable=False, default=0, server_default="0")
    views_7d = Column(Integer, nullable=False, default=0, server_default="0")

    # Unique visitor counts
    unique_visitors_24h = Column(Integer, nullable=False, default=0, server_default="0")
    unique_visitors_7d = Column(Integer, nullable=False, default=0, server_default="0")

    # Conversion signals
    cart_conversions_24h = Column(Integer, nullable=False, default=0, server_default="0")
    cart_conversions_7d = Column(Integer, nullable=False, default=0, server_default="0")

    # Device segmentation (24h window)
    views_mobile = Column(Integer, nullable=False, default=0, server_default="0")
    views_desktop = Column(Integer, nullable=False, default=0, server_default="0")
    carts_mobile = Column(Integer, nullable=False, default=0, server_default="0")
    carts_desktop = Column(Integer, nullable=False, default=0, server_default="0")

    # Source segmentation (24h window, 3 buckets: paid / organic / direct)
    views_paid = Column(Integer, nullable=False, default=0, server_default="0")
    views_organic = Column(Integer, nullable=False, default=0, server_default="0")
    views_direct = Column(Integer, nullable=False, default=0, server_default="0")
    carts_paid = Column(Integer, nullable=False, default=0, server_default="0")
    carts_organic = Column(Integer, nullable=False, default=0, server_default="0")
    carts_direct = Column(Integer, nullable=False, default=0, server_default="0")

    # Return visitor signal — distinct visitors who viewed this product
    # on 2+ distinct calendar days within the last 7 days.
    return_visitor_count_7d = Column(Integer, nullable=False, default=0, server_default="0")

    # Purchase attribution (via visitor_purchase_sessions + shop_orders)
    purchases_24h = Column(Integer, nullable=False, default=0, server_default="0")
    purchases_7d = Column(Integer, nullable=False, default=0, server_default="0")
    revenue_24h = Column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    purchases_mobile = Column(Integer, nullable=False, default=0, server_default="0")
    purchases_desktop = Column(Integer, nullable=False, default=0, server_default="0")
    purchases_paid = Column(Integer, nullable=False, default=0, server_default="0")
    purchases_organic = Column(Integer, nullable=False, default=0, server_default="0")
    purchases_direct = Column(Integer, nullable=False, default=0, server_default="0")

    # Time-of-day intelligence (24h window, peak = best 6h block)
    peak_hour_views = Column(Integer, nullable=False, default=0, server_default="0")
    peak_hour_carts = Column(Integer, nullable=False, default=0, server_default="0")
    off_peak_hour_views = Column(Integer, nullable=False, default=0, server_default="0")
    off_peak_hour_carts = Column(Integer, nullable=False, default=0, server_default="0")

    # Session context (24h window, landing = first product in session)
    landing_views_24h = Column(Integer, nullable=False, default=0, server_default="0")
    browsing_views_24h = Column(Integer, nullable=False, default=0, server_default="0")
    landing_carts_24h = Column(Integer, nullable=False, default=0, server_default="0")
    browsing_carts_24h = Column(Integer, nullable=False, default=0, server_default="0")

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
        default=utc_now_naive,
        onupdate=utc_now_naive,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_product_metrics_shop_product",
        ),
        Index("ix_product_metrics_shop_domain", "shop_domain"),
        Index(
            "ix_product_metrics_shop_visitors",
            "shop_domain",
            text("unique_visitors_24h DESC"),
        ),
        # Added 2026-05-08 (perf hunt): covers /pro/price-sensitivity which
        # filters WHERE shop_domain=:s AND views_7d >= 3. Partial index
        # (views_7d >= 3) keeps it small. See migration
        # aa1_pro_perf_composite_indexes.
        Index(
            "ix_product_metrics_shop_views_7d",
            "shop_domain",
            "views_7d",
            postgresql_where=text("views_7d >= 3"),
        ),
    )
