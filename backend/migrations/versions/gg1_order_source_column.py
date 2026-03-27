"""Add source column to shop_orders.

Tracks ingestion origin: 'pixel' (client-side Custom Pixel) or 'webhook'
(Shopify Admin API webhook).  Enables future webhook upsert: when a webhook
delivers richer data for a pixel-originated order, the row can be upgraded.

Revision ID: gg1_order_source_column
Revises: ff1_events_indexes
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = "gg1_order_source_column"
down_revision = "ff1_events_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shop_orders",
        sa.Column("source", sa.String(16), nullable=False, server_default="pixel"),
    )


def downgrade() -> None:
    op.drop_column("shop_orders", "source")
