"""s5e6f7a8b9c0_nudge_events

Nudge measurement table — exposure, dismissal, and interaction events.

Each row records one measurement event for one nudge × visitor pair:
  shown      — the nudge was rendered to this visitor on this product page
  dismissed  — the visitor clicked the dismiss (×) button
  clicked    — the visitor clicked a CTA (reserved for future nudge types with CTAs)

This table is the foundation for:
  - Counting real nudge exposures (not impressions — unique visitors who saw it)
  - Computing dismissal rates (dismissed / exposed)
  - Observational post-exposure purchase attribution:
      JOIN visitor_purchase_sessions ON visitor_id + shop_domain WHERE
      confirmed_at > first shown_at AND confirmed_at < first shown_at + 24h
  - Future autonomous agent optimization: scoring SCARCITY_NUDGE tasks by
    post-exposure CVR so agents can learn which segment conditions work best.

Design notes
------------
visitor_id IS nullable (NULL when localStorage is blocked).  Events with
NULL visitor_id contribute to aggregate counts but are excluded from
attribution joins.

nudge_id references active_nudges.id but the FK is NOT enforced — nudge
records may be deactivated or expired while their measurement events persist
for historical analysis.

Deduplication is handled client-side (sessionStorage key per nudge_id per
tab session).  The server stores all events received — no server-side unique
constraint on (nudge_id, visitor_id, event_type) because the same visitor
can see the same nudge in different tab sessions (each is a real exposure).

Indexes
-------
ix_nudge_events_shop_nudge_type  — primary stats query: counts by event_type for one nudge
ix_nudge_events_shop_visitor     — attribution join: all nudge exposures for one visitor
ix_nudge_events_created_at       — time-window queries: exposures in last N days

Revision ID: s5e6f7a8b9c0
Revises: r4d5e6f7a8b9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s5e6f7a8b9c0"
down_revision = "r4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nudge_events",
        sa.Column("id",           sa.Integer(),  nullable=False, primary_key=True),

        # Tenant scope
        sa.Column("shop_domain",  sa.String(),   nullable=False),

        # Which nudge — references active_nudges.id (non-enforced FK)
        sa.Column("nudge_id",     sa.Integer(),  nullable=False),

        # Pseudonymous visitor UUID — nullable when localStorage is blocked
        sa.Column("visitor_id",   sa.String(),   nullable=True),

        # Product page where the event occurred
        sa.Column("product_url",  sa.String(),   nullable=False),

        # shown | dismissed | clicked
        sa.Column("event_type",   sa.String(),   nullable=False),

        # Server receipt timestamp (UTC)
        sa.Column("created_at",   sa.DateTime(), nullable=False, server_default=sa.func.now()),

        # Optional JSON payload — copy_variant at time of show, UA, etc.
        # Named event_meta (not metadata) — reserved name in SQLAlchemy Declarative API
        sa.Column("event_meta",   sa.Text(),     nullable=True),
    )

    op.create_index(
        "ix_nudge_events_shop_nudge_type",
        "nudge_events",
        ["shop_domain", "nudge_id", "event_type"],
    )
    op.create_index(
        "ix_nudge_events_shop_visitor",
        "nudge_events",
        ["shop_domain", "visitor_id"],
    )
    op.create_index(
        "ix_nudge_events_created_at",
        "nudge_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_nudge_events_created_at",       table_name="nudge_events")
    op.drop_index("ix_nudge_events_shop_visitor",     table_name="nudge_events")
    op.drop_index("ix_nudge_events_shop_nudge_type",  table_name="nudge_events")
    op.drop_table("nudge_events")
