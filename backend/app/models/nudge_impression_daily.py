"""
nudge_impression_daily.py — Per-visitor, per-nudge, per-day impression dedup sentinel.

Purpose
-------
Prevents a page reload (or multiple tab opens within the same calendar day)
from inflating nudge "shown" impression counts in the A/B measurement pipeline.

Each row records that visitor_id saw nudge_id at least once on impression_date.
The UNIQUE constraint on (nudge_id, visitor_id, impression_date) is enforced
at the database level — INSERT ON CONFLICT DO NOTHING is used for atomicity.

If the INSERT affects 0 rows → duplicate impression → the NudgeEvent is
suppressed (not written to nudge_events).

If the INSERT affects 1 row → genuine first impression today → the NudgeEvent
is written as normal.

Design decisions
----------------
Separate table (not a unique index on nudge_events):
  - nudge_events is an event log; adding a functional unique index would
    lock the table schema for attribution and measurement queries.
  - The sentinel table is lightweight (4 integer/string cols) and trivially
    partitioned or purged.
  - Row lifetime matches measurement relevance: 30 days (configurable via
    cron or retention worker).

Visitor anonymity:
  - visitor_id is a localStorage pseudonymous UUID; no PII.
  - Rows with NULL visitor_id are not written (cannot dedup unknown visitors).
    Null-visitor events are still counted in nudge_events as before.

Scope of dedup:
  - Only "shown" events are deduplicated.
  - "dismissed" and "clicked" are interaction events and are not rate-limited
    here; one click per day per nudge is a legitimate and distinct signal.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Index, Integer, String, UniqueConstraint

from app.core.database import Base
from app.core.time_utils import utc_now_naive


class NudgeImpressionDaily(Base):
    __tablename__ = "nudge_impression_daily"

    id              = Column(Integer, primary_key=True, autoincrement=True)

    # Tenant scope
    shop_domain     = Column(String, nullable=False)

    # Which nudge — matches active_nudges.id (non-enforced FK)
    nudge_id        = Column(Integer, nullable=False)

    # Pseudonymous visitor UUID — never NULL (rows with no visitor_id are
    # not deduplicated and never written to this table)
    visitor_id      = Column(String, nullable=False)

    # UTC calendar date of the impression — boundary resets at UTC midnight
    impression_date = Column(Date, nullable=False)

    # Row creation timestamp — used for retention cleanup
    created_at      = Column(DateTime, nullable=False, default=utc_now_naive)

    __table_args__ = (
        # Dedup constraint — enforces one-impression-per-day at DB level.
        # INSERT ON CONFLICT DO NOTHING checks this atomically.
        UniqueConstraint(
            "nudge_id", "visitor_id", "impression_date",
            name="uq_nudge_impression_daily",
        ),
        # Shop-scoped cleanup index — allows efficient DELETE WHERE
        # shop_domain = ? AND impression_date < ? for retention jobs.
        Index("ix_nudge_impression_shop_date", "shop_domain", "impression_date"),
    )
