"""shop_orders: real Shopify order ingestion table

Revision ID: n3f5a6b7c8d9
Revises: m2d3e4f5a6b7
Create Date: 2026-03-21

Introduces shop_orders — the real revenue layer.

Replaces the hardcoded DEFAULT_AOV = 50.0 fallback used everywhere in the
conversion and revenue-loss pipeline.  With this table populated:

  - AOV is computable per shop:   AVG(total_price) WHERE shop_domain = shop
  - Conversion rate is real:      COUNT(orders) / COUNT(unique_visitor_sessions)
  - Revenue attribution possible: join line_items on product_url/product_id
  - Feedback loop is possible:    compare product_metrics before/after a task

Columns
-------
  shop_domain        — tenant scope
  shopify_order_id   — Shopify's ID, UNIQUE for idempotent webhook ingestion
  total_price        — order total in merchant currency
  currency           — ISO 4217 currency code
  customer_id        — Shopify customer ID (NULL for guest checkout)
  line_items         — JSONB array of raw Shopify line item objects
  created_at         — Shopify-side timestamp (for revenue time-window queries)
  ingested_at        — server receipt timestamp (for dedup audit)

Indexes
-------
  uq_shop_orders_shopify_order_id   — UNIQUE, dedup constraint
  ix_shop_orders_shop_domain        — per-shop listing
  ix_shop_orders_shop_created       — time-scoped revenue queries
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n3f5a6b7c8d9"
down_revision: Union[str, None] = "m2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shop_orders",
        sa.Column("id",                sa.Integer(),    nullable=False),
        sa.Column("shop_domain",       sa.String(),     nullable=False),
        sa.Column("shopify_order_id",  sa.String(),     nullable=False),
        sa.Column("total_price",       sa.Float(),      nullable=False),
        sa.Column("currency",          sa.String(),     nullable=False, server_default="EUR"),
        sa.Column("customer_id",       sa.String(),     nullable=True),
        sa.Column("line_items",        postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at",        sa.DateTime(),   nullable=False),
        sa.Column("ingested_at",       sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shopify_order_id", name="uq_shop_orders_shopify_order_id"),
    )
    op.create_index("ix_shop_orders_shop_domain",  "shop_orders", ["shop_domain"])
    op.create_index("ix_shop_orders_shop_created", "shop_orders", ["shop_domain", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_shop_orders_shop_created", table_name="shop_orders")
    op.drop_index("ix_shop_orders_shop_domain",  table_name="shop_orders")
    op.drop_table("shop_orders")
