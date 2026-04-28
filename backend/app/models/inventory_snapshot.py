"""InventorySnapshot — Gap #4 daily inventory pipeline.

One row per (shop, product, variant, day). Written by the
aggregation_worker daily phase. Read by /merchant/inventory/* for
KPIs and the Stock health card.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from app.core.database import Base


def _now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "shop_domain",
            "product_url",
            "variant_id",
            "snapshot_date",
            name="uq_inventory_shop_product_variant_date",
        ),
        Index(
            "idx_inventory_shop_date",
            "shop_domain",
            text("snapshot_date DESC"),
        ),
        Index(
            "idx_inventory_shop_product_latest",
            "shop_domain",
            "product_url",
            text("snapshot_date DESC"),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    shop_domain = Column(String, nullable=False)
    product_url = Column(String, nullable=False)
    product_title = Column(String, nullable=True)
    variant_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    inventory_quantity = Column(Integer, nullable=False)
    snapshot_date = Column(Date, nullable=False)
    fetched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        server_default=text("now()"),
    )
