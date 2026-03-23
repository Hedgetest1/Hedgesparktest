"""Add product_id column to events table.

Stores the Shopify numeric product_id captured by spark-tracker.js from
window.ShopifyAnalytics.meta.product.id on product pages.

Used by order_ingestion.py to resolve product_url at webhook time:
    SELECT DISTINCT product_url FROM events
    WHERE shop_domain = :shop AND product_id = :pid

This closes the product_id → product_url gap that prevents
get_real_product_conversion_map() from returning real data.

Revision ID: o1a2b3c4d5e6
Revises: n3f5a6b7c8d9
"""
from alembic import op
import sqlalchemy as sa

revision = "o1a2b3c4d5e6"
down_revision = "n3f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("product_id", sa.String(length=64), nullable=True),
    )
    # Partial index — only rows where product_id is set, keeping index small.
    op.create_index(
        "ix_events_shop_product_id",
        "events",
        ["shop_domain", "product_id"],
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_events_shop_product_id", table_name="events")
    op.drop_column("events", "product_id")
