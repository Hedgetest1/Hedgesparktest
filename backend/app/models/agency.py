"""
agency.py — Phase Ω'' white-label / agency mode.

Agencies and consultancies manage many merchants on behalf of clients.
This model lets one agency account see a roster of client shops and
their KPIs from a single pane of glass, with a revenue-share split
recorded per client.

Two tables:
  * agencies            — one row per agency
  * agency_clients      — many-to-one linkage to merchant shops
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class Agency(Base):
    __tablename__ = "agencies"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    contact_email = Column(String, nullable=False, index=True, unique=True)

    # The agency's brand is shown to its clients in white-label dashboards
    brand_color = Column(String(8), nullable=True)         # hex
    logo_url = Column(String(500), nullable=True)
    custom_subdomain = Column(String(120), nullable=True, unique=True)

    # Default revenue-share applied when adding a client
    default_revshare_pct = Column(Float, nullable=False, default=20.0, server_default="20.0")

    created_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()", onupdate=utc_now_naive)


class AgencyClient(Base):
    __tablename__ = "agency_clients"

    id = Column(Integer, primary_key=True)
    agency_id = Column(Integer, ForeignKey("agencies.id"), nullable=False, index=True)
    shop_domain = Column(String, nullable=False, index=True)

    nickname = Column(String(200), nullable=True)
    revshare_pct = Column(Float, nullable=False, default=20.0, server_default="20.0")

    status = Column(String(16), nullable=False, default="active", server_default="active")  # active|paused|removed
    onboarded_at = Column(DateTime, nullable=False, default=utc_now_naive, server_default="now()")

    __table_args__ = (
        UniqueConstraint("agency_id", "shop_domain", name="uq_agency_client_agency_shop"),
        Index("ix_agency_client_status", "agency_id", "status"),
    )
