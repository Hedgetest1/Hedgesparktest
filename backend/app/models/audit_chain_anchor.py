"""audit_chain_anchor — singleton DB-side anchor of the audit_log chain head.

The audit log uses a forward hash chain. Historically the chain head
was anchored only in Redis (`hs:audit_log:chain_head`). Threat model:
an attacker with DB+Redis write access could wipe both and reconstruct
a fake chain from genesis — verification would pass because the Redis
cross-check returns None when the key is missing.

This table is the DB-side defense. Singleton (PK constrained to 1).
Updated inside the pg_advisory_xact_lock that serializes audit writes,
so the anchor stays consistent with the DB-resident last-row chain
under concurrent writers.

Born 2026-05-15 (10k-structural sprint, TIER_2 fresh approval).
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, text

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class AuditChainAnchor(Base):
    __tablename__ = "audit_chain_anchor"

    id = Column(Integer, primary_key=True)
    chain_head = Column(String(64), nullable=False)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        server_default=text("now()"),
    )
    revision_counter = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="audit_chain_anchor_singleton"),
    )
