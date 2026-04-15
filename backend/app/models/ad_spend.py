"""
ad_spend.py — Phase Ω ecosystem #2.

Unified ad spend table across networks (Meta, Google, TikTok, ...).
One row per (shop, date, network, campaign_id) — daily granularity is
enough for HedgeSpark's MTA + RARS use cases and keeps storage cheap.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Index, Integer, Numeric, String, UniqueConstraint

from app.core.database import Base


class AdSpendDaily(Base):
    __tablename__ = "ad_spend_daily"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False)

    network = Column(String(16), nullable=False)  # meta | google | tiktok | other
    campaign_id = Column(String(64), nullable=False)
    campaign_name = Column(String(200), nullable=True)

    spend_eur = Column(Numeric(18, 2), nullable=False, default=0)
    impressions = Column(Integer, nullable=False, default=0)
    clicks = Column(Integer, nullable=False, default=0)
    conversions = Column(Integer, nullable=False, default=0)
    revenue_attributed_eur = Column(Numeric(18, 2), nullable=False, default=0)

    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("shop_domain", "date", "network", "campaign_id",
                         name="uq_ad_spend_shop_date_net_camp"),
        Index("ix_ad_spend_shop_date", "shop_domain", "date"),
        Index("ix_ad_spend_shop_network", "shop_domain", "network"),
    )


class AdConnection(Base):
    """Encrypted credential reference per (shop, network)."""
    __tablename__ = "ad_connections"

    id = Column(Integer, primary_key=True)
    shop_domain = Column(String, nullable=False, index=True)
    network = Column(String(16), nullable=False)  # meta | google | tiktok

    # We never store raw tokens here; we store an opaque token id that
    # references the encrypted credential vault entry. For Phase Ω the
    # credential vault hookup is stubbed — connection rows still record
    # which networks a merchant has connected so the UI can show status.
    credential_ref = Column(String(128), nullable=True)
    account_id = Column(String(128), nullable=True)
    account_name = Column(String(200), nullable=True)

    status = Column(String(16), nullable=False, default="connected")  # connected|disconnected|error
    last_synced_at = Column(DateTime, nullable=True)
    last_error = Column(String(500), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("shop_domain", "network", name="uq_ad_conn_shop_network"),
    )
