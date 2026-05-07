"""
PredictionLog — persisted forecast/LTV/RARS predictions + their actuals.

Powers the MA-1 prediction-accuracy moat: every call to our
probabilistic-forecast surfaces writes one row; a backtest walks
matured rows and computes MAPE to publish honest accuracy numbers.
Competitors don't publish accuracy; we do.

The write path is append-only by convention (via log_prediction()
helper), but the row is later UPDATE'd in place to fill in
actual_value + measured_at once the horizon_date has passed. This
differs from audit_log which is strictly immutable — hence the
dedicated table.

Uniqueness: (shop_domain, metric, horizon_date) so duplicate forecasts
on the same day don't double-count in the accuracy math.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PredictionLog(Base):
    __tablename__ = "prediction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime, nullable=False, default=_now_utc, server_default=text("now()")
    )

    shop_domain = Column(String, nullable=False)
    metric = Column(String(64), nullable=False)
    prediction_date = Column(Date, nullable=False)
    horizon_date = Column(Date, nullable=False)

    predicted_value = Column(Numeric(18, 2), nullable=False)
    predicted_low = Column(Numeric(18, 2), nullable=True)
    predicted_high = Column(Numeric(18, 2), nullable=True)

    currency = Column(String(8), nullable=False, server_default="'USD'")
    confidence = Column(String(16), nullable=True)

    actual_value = Column(Numeric(18, 2), nullable=True)
    measured_at = Column(DateTime, nullable=True)

    context_hash = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "shop_domain", "metric", "horizon_date",
            name="uq_prediction_log_shop_metric_horizon",
        ),
        Index(
            "ix_prediction_log_matured",
            "shop_domain", "horizon_date",
            postgresql_where="actual_value IS NULL",
        ),
        Index(
            "ix_prediction_log_shop_metric_created",
            "shop_domain", "metric", "created_at",
        ),
    )
