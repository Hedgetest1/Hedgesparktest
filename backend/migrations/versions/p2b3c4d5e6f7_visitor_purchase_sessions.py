"""Create visitor_purchase_sessions table.

Bridges visitor behavioral identity (visitor_id from localStorage) to real
Shopify orders (shopify_order_id from the orders/paid webhook and the
thank-you page attribution script).

This table is the foundation for:
  - Empirical per-product conversion rate computation
  - Behavioral profiling of converting vs non-converting visitors
  - Retargeting candidate identification
  - Feedback measurement after agent-executed actions

Revision ID: p2b3c4d5e6f7
Revises: o1a2b3c4d5e6
"""
from alembic import op
import sqlalchemy as sa

revision = "p2b3c4d5e6f7"
down_revision = "o1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visitor_purchase_sessions",

        sa.Column("id", sa.Integer(), nullable=False),

        # Tenant scope — always filter by shop_domain first
        sa.Column("shop_domain", sa.String(), nullable=False),

        # The persistent visitor UUID written by spark-tracker.js to localStorage.
        # Matches visitors.visitor_id and events.visitor_id.
        sa.Column("visitor_id", sa.String(), nullable=False),

        # Shopify's order ID — unique per Shopify installation.
        # Matches shop_orders.shopify_order_id.
        # UNIQUE constraint enforces idempotency: refreshing the thank-you page
        # or duplicate spark-attribution.js fires produce a single row.
        sa.Column("shopify_order_id", sa.String(), nullable=False),

        # The product URL the visitor was last attributed to before purchase.
        # NULL in v1 — populated in future by joining with shop_orders.line_items
        # or by capturing the last product_url from events at attribution time.
        # Nullable intentionally: do not invent attribution when uncertain.
        sa.Column("product_url", sa.String(), nullable=True),

        # When the thank-you page script fired (browser timestamp → epoch ms,
        # stored as DateTime for readability and range queries).
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),

        # Server receipt time — use for audit, dedup, and ingestion lag measurement.
        sa.Column("ingested_at", sa.DateTime(), nullable=False),

        sa.PrimaryKeyConstraint("id"),

        # Idempotency: one attribution row per order, ever.
        sa.UniqueConstraint("shopify_order_id", name="uq_vps_shopify_order_id"),
    )

    # Per-shop visitor lookup: "all orders attributed to this visitor"
    op.create_index(
        "ix_vps_shop_visitor",
        "visitor_purchase_sessions",
        ["shop_domain", "visitor_id"],
    )

    # Per-shop order lookup: "find the visitor who generated this order"
    op.create_index(
        "ix_vps_shop_order",
        "visitor_purchase_sessions",
        ["shop_domain", "shopify_order_id"],
    )

    # Time-scoped queries for behavioral profile learning
    op.create_index(
        "ix_vps_shop_confirmed",
        "visitor_purchase_sessions",
        ["shop_domain", "confirmed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vps_shop_confirmed", table_name="visitor_purchase_sessions")
    op.drop_index("ix_vps_shop_order", table_name="visitor_purchase_sessions")
    op.drop_index("ix_vps_shop_visitor", table_name="visitor_purchase_sessions")
    op.drop_table("visitor_purchase_sessions")
