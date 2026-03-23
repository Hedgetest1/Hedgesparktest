"""feat: add source_type and referrer columns to events table

Revision ID: j7e0a4b8c3d6
Revises: i6d9f3a7b2c5
Create Date: 2026-03-20

Problem
-------
The widget (wishspark.js) captures source_type and referrer on every event
and sends them in the POST payload.  The backend schema (EventCreate) already
accepts these fields, but the Event ORM model has no corresponding columns,
so the values were silently discarded and never persisted to the database.

spark-tracker.js did not capture source_type or referrer at all.

Both issues are fixed alongside this migration:
  - Event model gains source_type and referrer columns (this migration).
  - track.py and events.py are updated to persist both fields.
  - spark-tracker.js is updated to detect and send both fields.

Solution
--------
Add two nullable VARCHAR columns to the events table:

  source_type  — one of: direct | search | social | referral
                 Classified from document.referrer at the tracker layer.
                 NULL for rows ingested before this migration.

  referrer     — raw document.referrer string (may be empty string or NULL).
                 NULL for rows ingested before this migration.

Both columns are nullable — no backfill is required for historical rows.
Historical rows simply lack source attribution, which is handled gracefully
by the /analytics/source-quality endpoint (NULL source_type → 'unknown').

Schema changes
--------------
  events:
    ADD COLUMN source_type  VARCHAR  NULL
    ADD COLUMN referrer     VARCHAR  NULL
    ADD INDEX  ix_events_source_type  (shop_domain, source_type)
      — supports the GROUP BY source_type query in source_quality endpoint
"""

from alembic import op
import sqlalchemy as sa


revision = "j7e0a4b8c3d6"
down_revision = "i6d9f3a7b2c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # source_type: classified traffic channel from document.referrer.
    # NULL = unknown (pre-migration row or tracker that did not send the field).
    op.add_column(
        "events",
        sa.Column("source_type", sa.String(), nullable=True),
    )

    # referrer: raw document.referrer sent by the tracker.
    # Empty string and NULL are both treated as "no referrer" in queries.
    op.add_column(
        "events",
        sa.Column("referrer", sa.String(), nullable=True),
    )

    # Composite index on (shop_domain, source_type) to keep the GROUP BY
    # in /analytics/source-quality fast even as the events table grows.
    op.create_index(
        "ix_events_shop_source_type",
        "events",
        ["shop_domain", "source_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_events_shop_source_type", table_name="events")
    op.drop_column("events", "referrer")
    op.drop_column("events", "source_type")
