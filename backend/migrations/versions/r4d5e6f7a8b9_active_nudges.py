"""r4d5e6f7a8b9_active_nudges

Active nudges table — storefront nudge execution artifacts.

Each row represents a live nudge configuration that the storefront script
polls on product page loads.  The segment_monitor_worker creates/refreshes
nudge rows when hot-segment conditions are met; the aggregation_worker sweeps
and expires stale rows on every cycle.

Indexes
-------
ix_active_nudges_shop_product  — primary lookup for /nudges/active endpoint
ix_active_nudges_shop_status   — dashboard listing: all active nudges per shop
ix_active_nudges_expires_at    — expiry sweep: DELETE WHERE expires_at < now()

Revision ID: r4d5e6f7a8b9
Revises: q3c4d5e6f7a8
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r4d5e6f7a8b9"
down_revision = "q3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "active_nudges",
        sa.Column("id",           sa.Integer(),    nullable=False, primary_key=True),
        sa.Column("shop_domain",  sa.String(),     nullable=False),
        sa.Column("product_url",  sa.String(),     nullable=False),
        sa.Column("action_type",  sa.String(),     nullable=False),
        sa.Column("trigger_source", sa.String(),   nullable=False),

        # Storefront copy — what the client renders
        sa.Column("copy_variant", sa.String(),     nullable=False),
        sa.Column("copy_config",  sa.Text(),       nullable=False),  # JSON string

        # Lifecycle
        sa.Column("status",       sa.String(),     nullable=False, server_default="active"),
        sa.Column("created_at",   sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",   sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at",   sa.DateTime(),   nullable=False),
        sa.Column("deactivated_at", sa.DateTime(), nullable=True),

        # Linkage to the action_task that triggered this nudge
        sa.Column("action_task_id", sa.Integer(),  nullable=True),

        # Segment context at the time this nudge was created/last refreshed
        sa.Column("visitor_count",            sa.Integer(), nullable=True),
        sa.Column("estimated_revenue_window", sa.Float(),   nullable=True),
        sa.Column("calibration_state",        sa.String(),  nullable=True),
    )

    op.create_index(
        "ix_active_nudges_shop_product",
        "active_nudges",
        ["shop_domain", "product_url"],
    )
    op.create_index(
        "ix_active_nudges_shop_status",
        "active_nudges",
        ["shop_domain", "status"],
    )
    op.create_index(
        "ix_active_nudges_expires_at",
        "active_nudges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_active_nudges_expires_at",    table_name="active_nudges")
    op.drop_index("ix_active_nudges_shop_status",   table_name="active_nudges")
    op.drop_index("ix_active_nudges_shop_product",  table_name="active_nudges")
    op.drop_table("active_nudges")
