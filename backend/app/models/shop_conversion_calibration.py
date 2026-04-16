"""
shop_conversion_calibration.py — Per-shop empirical conversion calibration record.

One row per shop.  Updated lazily when the record is stale (>6 hours) or on
explicit retrain.  Read-only at inference time.

See app/services/empirical_calibration.py for the training and application logic.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, UniqueConstraint

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class ShopConversionCalibration(Base):
    __tablename__ = "shop_conversion_calibrations"

    id = Column(Integer, primary_key=True)

    # One row per shop
    shop_domain = Column(String, nullable=False)

    # Increments on each retrain — useful for audit and debugging
    model_version = Column(Integer, nullable=False, default=1, server_default="1")

    # Training window
    lookback_days = Column(Integer, nullable=False, default=30, server_default="30")

    # Training dataset counts
    sample_size     = Column(Integer, nullable=False)   # total product-viewing visitors
    converter_count = Column(Integer, nullable=False)   # attributed purchasers

    # Core calibration parameters
    # base_cvr: shop-wide empirical conversion rate = converters / sample_size
    base_cvr = Column(Float, nullable=False)

    # behavioral_index statistics per cohort (see empirical_calibration._compute_behavioral_index)
    converter_behavioral_mean     = Column(Float, nullable=False)
    non_converter_behavioral_mean = Column(Float, nullable=False)

    # discriminability = converter_mean - non_converter_mean
    # > 0: behavioral index predicts conversion in the expected direction
    # near 0: behavioral features are not discriminating for this shop
    discriminability = Column(Float, nullable=False)

    # True when data meets minimum thresholds for empirical use.
    # False → model is in FALLBACK mode; apply_calibration() returns inferred unchanged.
    is_empirical = Column(Boolean, nullable=False, default=False, server_default="false")

    # Last training timestamp — used for staleness check in get_or_train_model()
    trained_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")

    __table_args__ = (
        UniqueConstraint("shop_domain", name="uq_scc_shop_domain"),
        Index("ix_scc_shop_domain", "shop_domain"),
    )
