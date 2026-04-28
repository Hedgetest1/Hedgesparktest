"""MerchantSavedReport — Gap #1 Custom Report Builder.

One row per saved custom report config. Report execution reads
pre-aggregated tables, never raw events.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MerchantSavedReport(Base):
    __tablename__ = "merchant_saved_reports"
    __table_args__ = (
        Index(
            "idx_msr_shop_updated",
            "shop_domain",
            text("updated_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_msr_scheduled",
            "shop_domain",
            postgresql_where=text("scheduled = true AND deleted_at IS NULL"),
        ),
        Index(
            "uq_msr_shop_name",
            "shop_domain",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_msr_shop_cadence",
            "shop_domain",
            "scheduled_cadence",
            unique=True,
            postgresql_where=text(
                "scheduled = true AND deleted_at IS NULL"
            ),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    shop_domain = Column(String, nullable=False)
    name = Column(String(60), nullable=False)
    metric = Column(String(40), nullable=False)
    dimensions = Column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    filters = Column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    date_range_preset = Column(
        String(32),
        nullable=False,
        default="last_30_days",
        server_default=text("'last_30_days'"),
    )
    custom_start = Column(Date, nullable=True)
    custom_end = Column(Date, nullable=True)
    compare_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    formula = Column(Text, nullable=True)
    forecast_horizon = Column(Integer, nullable=True)
    scheduled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    scheduled_cadence = Column(String(16), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        server_default=text("now()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        server_default=text("now()"),
    )
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
