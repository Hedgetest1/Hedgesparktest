"""nudge_impression_daily — per-visitor per-nudge per-day impression dedup

Revision ID: x1c2d3e4f5a6
Revises: w2b3c4d5e6f7
Create Date: 2026-03-23

Purpose
-------
Adds the nudge_impression_daily table, which serves as a dedup sentinel
for nudge "shown" events.

For each (nudge_id, visitor_id, impression_date) triple, only one NudgeEvent
row is written per UTC calendar day.  A page reload or duplicate render within
the same day does not inflate impression counts in the A/B measurement pipeline.

Key design choices
------------------
- UNIQUE constraint on (nudge_id, visitor_id, impression_date):
    enforced at DB level; INSERT ON CONFLICT DO NOTHING is atomic under
    concurrent requests and requires no application-level locking.

- Index on (shop_domain, impression_date):
    enables O(n) retention cleanup:
    DELETE FROM nudge_impression_daily WHERE shop_domain=? AND impression_date < ?

Idempotency
-----------
Uses IF NOT EXISTS / inspection guards so the migration is safe to run even
when Base.metadata.create_all has already created the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision = "x1c2d3e4f5a6"
down_revision = "w2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    existing_tables = inspector.get_table_names()

    if "nudge_impression_daily" not in existing_tables:
        op.create_table(
            "nudge_impression_daily",
            sa.Column("id",              sa.Integer(),  primary_key=True, autoincrement=True),
            sa.Column("shop_domain",     sa.String(),   nullable=False),
            sa.Column("nudge_id",        sa.Integer(),  nullable=False),
            sa.Column("visitor_id",      sa.String(),   nullable=False),
            sa.Column("impression_date", sa.Date(),     nullable=False),
            sa.Column("created_at",      sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    # Add constraints/indexes idempotently
    existing_constraints = [c["name"] for c in inspector.get_unique_constraints("nudge_impression_daily")]
    if "uq_nudge_impression_daily" not in existing_constraints:
        op.create_unique_constraint(
            "uq_nudge_impression_daily",
            "nudge_impression_daily",
            ["nudge_id", "visitor_id", "impression_date"],
        )

    existing_indexes = [i["name"] for i in inspector.get_indexes("nudge_impression_daily")]
    if "ix_nudge_impression_shop_date" not in existing_indexes:
        op.create_index(
            "ix_nudge_impression_shop_date",
            "nudge_impression_daily",
            ["shop_domain", "impression_date"],
        )


def downgrade() -> None:
    op.drop_index("ix_nudge_impression_shop_date", table_name="nudge_impression_daily")
    op.drop_constraint("uq_nudge_impression_daily", "nudge_impression_daily", type_="unique")
    op.drop_table("nudge_impression_daily")
