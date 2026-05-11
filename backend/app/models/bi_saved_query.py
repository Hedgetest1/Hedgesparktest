"""bi_saved_query.py — Pro #3 BI Query Builder saved query."""
from __future__ import annotations

from sqlalchemy import (
    Column, BigInteger, String, DateTime, Index, CheckConstraint,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class BiSavedQuery(Base):
    __tablename__ = "bi_saved_queries"

    id = Column(BigInteger, primary_key=True)
    shop_domain = Column(String, nullable=False)
    name = Column(String(128), nullable=False)
    query_json = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime, nullable=False, server_default=text("now()"),
    )
    updated_at = Column(
        DateTime, nullable=False, server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "shop_domain", "name",
            name="uq_bi_saved_queries_shop_name",
        ),
        CheckConstraint(
            "length(name) BETWEEN 1 AND 128",
            name="bi_saved_queries_name_check",
        ),
        Index("ix_bi_saved_queries_shop", "shop_domain", "updated_at"),
    )
