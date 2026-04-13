"""
analytics_event.py — ClickHouse-shaped internal event store (β6).

THE PATH to 10k merchants at scale. Today Postgres. Tomorrow ClickHouse.
Same API. Same schema. Zero rewrites at call sites.

Design
------
This table is the landing zone for all high-cardinality events that
need fast OLAP aggregation (visitor tracking, conversion funnels,
attribution paths). It is **additive** — the existing `events` table
stays exactly where it is and keeps serving the storefront tracker.

When we're ready to move to ClickHouse:
  1. Point the bus producer at a Kafka/Redpanda topic (or direct
     ClickHouse HTTP insert batching endpoint)
  2. Flip the EVENT_BUS_BACKEND env var from "postgres" to "clickhouse"
  3. Consumers read via the same `query_analytics_events()` helper —
     the SQL is purposefully compatible with ClickHouse-flavor SQL

Schema — deliberately denormalized for fast aggregation
-------------------------------------------------------
id           BIGSERIAL PRIMARY KEY
ts_ms        BIGINT   NOT NULL  — epoch milliseconds (ClickHouse DateTime64)
event_name   VARCHAR  NOT NULL  — e.g. 'page_view', 'add_to_cart', 'nudge_shown'
shop_domain  VARCHAR  NOT NULL  — tenant
visitor_id   VARCHAR  NULL      — hashable visitor UUID
session_id   VARCHAR  NULL      — per-session id
source       VARCHAR  NULL      — 'direct', 'google', 'meta', ...
campaign     VARCHAR  NULL      — utm_campaign
product_url  VARCHAR  NULL
revenue_eur  FLOAT    NULL      — for revenue-bearing events
props        JSONB    NULL      — flexible extra fields

Indexes
-------
(shop_domain, ts_ms DESC)  — hot path for dashboard queries
(shop_domain, event_name, ts_ms DESC)  — event-type drilldown
(visitor_id)              — journey reconstruction

Partitioning
------------
TODO on ClickHouse migration: PARTITION BY toYYYYMM(ts_ms).
For Postgres today, we'll run a monthly cleanup job that deletes
rows older than 90d (retention policy — TIER_0 cleanup in worker).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(BigInteger, primary_key=True)
    ts_ms = Column(BigInteger, nullable=False)
    event_name = Column(String(64), nullable=False)
    shop_domain = Column(String, nullable=False)

    visitor_id = Column(String, nullable=True)
    session_id = Column(String, nullable=True)

    source = Column(String(64), nullable=True)
    campaign = Column(String(256), nullable=True)
    product_url = Column(String(512), nullable=True)

    revenue_eur = Column(Float, nullable=True)

    props = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_analytics_shop_ts", "shop_domain", "ts_ms"),
        Index("ix_analytics_shop_event_ts", "shop_domain", "event_name", "ts_ms"),
        Index("ix_analytics_visitor", "visitor_id"),
    )
