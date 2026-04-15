"""
merchant_group.py — Phase Ω'' multi-store consolidation.

A merchant group ties multiple Shopify shops to one founder identity so
they can be viewed as a single brand.  E.g. a fashion house running an
Italian and a German Shopify store sees one consolidated dashboard.

Two tables:
  * merchant_groups            — one row per group
  * merchant_group_members     — many-to-one membership rows
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class MerchantGroup(Base):
    __tablename__ = "merchant_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    owner_email = Column(String, nullable=False, index=True)
    description = Column(String(500), nullable=True)

    # Currency for consolidated reporting — defaults to EUR.
    base_currency = Column(String(8), nullable=False, default="EUR")

    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class MerchantGroupMember(Base):
    __tablename__ = "merchant_group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("merchant_groups.id"), nullable=False, index=True)
    shop_domain = Column(String, nullable=False, index=True)

    # Display label inside the group (e.g. "EU store", "US store")
    label = Column(String(120), nullable=True)

    is_primary = Column(Boolean, nullable=False, default=False)
    added_at = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        UniqueConstraint("group_id", "shop_domain", name="uq_mgm_group_shop"),
        Index("ix_mgm_shop", "shop_domain"),
    )
