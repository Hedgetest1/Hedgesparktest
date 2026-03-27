"""Add performance indexes to events table.

Revision ID: ff1_events_indexes
Revises: ee1_session_version
Create Date: 2026-03-25
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import engine_from_config
from alembic import context

revision = "ff1_events_indexes"
down_revision = "ee1_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    existing = {idx["name"] for idx in inspector.get_indexes("events")}

    if "ix_events_shop_type_ts" not in existing:
        op.create_index(
            "ix_events_shop_type_ts",
            "events",
            ["shop_domain", "event_type", "timestamp"],
        )
    if "ix_events_shop_visitor" not in existing:
        op.create_index(
            "ix_events_shop_visitor",
            "events",
            ["shop_domain", "visitor_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_events_shop_visitor", table_name="events")
    op.drop_index("ix_events_shop_type_ts", table_name="events")
