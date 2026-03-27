"""Add action_snapshots table for closed-loop proof-of-impact.

Captures baseline metrics when an action is created, enabling
"Before & After" comparison after the merchant acts.

Revision ID: jj1_action_snapshots
Revises: ii1_merchant_pixel_secret
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "jj1_action_snapshots"
down_revision = "ii1_merchant_pixel_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False),
        sa.Column("product_url", sa.String, nullable=False),
        sa.Column("action_type", sa.String, nullable=False),
        sa.Column("action_task_id", sa.Integer, nullable=True),
        # Baseline metrics captured at snapshot time
        sa.Column("baseline_cvr", sa.Float, nullable=True),
        sa.Column("baseline_atc_rate", sa.Float, nullable=True),
        sa.Column("baseline_revenue_7d", sa.Float, nullable=True),
        sa.Column("baseline_visitors_7d", sa.Integer, nullable=True),
        sa.Column("baseline_orders_7d", sa.Integer, nullable=True),
        # Signal that triggered this snapshot
        sa.Column("signal_type", sa.String, nullable=True),
        sa.Column("signal_strength", sa.Float, nullable=True),
        # Lifecycle
        sa.Column("snapshot_at", sa.DateTime, nullable=False),
        sa.Column("compare_after", sa.DateTime, nullable=False),  # snapshot_at + 7 days
        sa.Column("delta_computed", sa.Boolean, nullable=False, server_default="false"),
        # Delta results (populated after compare_after date)
        sa.Column("delta_cvr", sa.Float, nullable=True),
        sa.Column("delta_atc_rate", sa.Float, nullable=True),
        sa.Column("delta_revenue_7d", sa.Float, nullable=True),
        sa.Column("delta_visitors_7d", sa.Integer, nullable=True),
        sa.Column("delta_orders_7d", sa.Integer, nullable=True),
        sa.Column("delta_computed_at", sa.DateTime, nullable=True),
        # Summary for display
        sa.Column("outcome", sa.String, nullable=True),  # improved | declined | stable
        sa.Column("summary", sa.String, nullable=True),  # human-readable
    )
    op.create_index("ix_snapshots_shop_product", "action_snapshots", ["shop_domain", "product_url"])
    op.create_index("ix_snapshots_compare", "action_snapshots", ["delta_computed", "compare_after"])


def downgrade() -> None:
    op.drop_table("action_snapshots")
