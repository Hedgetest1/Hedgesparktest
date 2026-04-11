"""sprint b — cost config tables for Profit Intelligence

Adds two tables the v2 pnl_engine reads from to replace module-level defaults:

    shop_cost_defaults
      Per-shop global cost assumptions. When set, override the hardcoded
      module constants in app/services/pnl_engine.py. Every field is
      nullable so merchants can partially-configure (e.g. only set COGS %,
      leave shipping at default).

    product_costs
      Per-product real COGS and shipping. When present, take precedence over
      the shop-level defaults for orders containing that product_key. Links
      to the product via the same product_key the rest of the codebase uses
      (Shopify product_id as string, or canonical product_url path).

Both tables are merchant-scoped via shop_domain with unique constraints on
(shop_domain, product_key) for product_costs and shop_domain primary key on
shop_cost_defaults. No cross-tenant leakage possible.

Non-destructive migration: both tables are new, no existing data touched.
Downgrade drops both tables cleanly.

Revision ID: sprint_b_cost_config
Revises: sip7_experiment_isolation
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = "sprint_b_cost_config"
down_revision = "sip7_experiment_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- shop_cost_defaults --------------------------------------------------
    # Per-shop global cost assumptions. One row per shop, merged with module
    # defaults in pnl_engine at compute time. Every column is NULLABLE so
    # partial configuration is allowed (e.g. merchant sets COGS % but leaves
    # shipping at the hardcoded default).
    op.create_table(
        "shop_cost_defaults",
        sa.Column("shop_domain", sa.String(), primary_key=True),

        # COGS % fallback when no per-product cost exists.
        # Stored as fraction (0.40 = 40%), matches pnl_engine._DEFAULT_COGS_PCT.
        sa.Column("default_cogs_pct", sa.Numeric(6, 4), nullable=True),

        # Flat shipping cost per order when no per-product shipping set.
        # Expressed in the shop's native currency.
        sa.Column("default_shipping_per_order", sa.Numeric(10, 2), nullable=True),

        # Payment processor rates — configurable because merchants on Stripe,
        # non-Shopify processors, or high-volume Shopify Plans have different
        # cost structures. Defaults in the row when NULL come from the
        # pnl_engine module constants (2.9% + 0.30).
        sa.Column("payment_pct",  sa.Numeric(6, 4), nullable=True),
        sa.Column("payment_flat", sa.Numeric(10, 2), nullable=True),

        # Rough monthly ad spend — merchant-entered manual figure used as a
        # bridge until Sprint B Phase 3 wires Meta Ads + Google Ads OAuth.
        # NULL means "no manual spend entered, ad spend reported as zero".
        sa.Column("ad_spend_manual_monthly", sa.Numeric(12, 2), nullable=True),

        # Display currency for all numeric fields above.
        sa.Column("currency", sa.String(8), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=False),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=False),
                  nullable=False, server_default=sa.text("NOW()")),
    )

    # --- product_costs -------------------------------------------------------
    # Per-product real COGS and fulfillment costs. Merchant provides these
    # manually via the Settings UI (Phase 2) or via CSV bulk import (Phase 2b).
    # Phase 3 can auto-populate from Shopify Admin API product cost field.
    op.create_table(
        "product_costs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(), nullable=False, index=True),

        # product_key matches the identifier used in shop_orders.line_items
        # and events.product_url. Either a Shopify numeric product_id as
        # string, or a canonical /products/{handle} path. pnl_engine joins
        # on this column when computing real COGS per order.
        sa.Column("product_key", sa.String(), nullable=False),

        # Denormalized product title for display in the Settings UI without
        # needing a second query to product_metrics or shop_orders.
        sa.Column("product_title", sa.String(), nullable=True),

        # Real cost per unit — the killer piece of data. NULL means "merchant
        # started a row but hasn't entered COGS yet"; pnl_engine treats NULL
        # same as "no row" and falls back to shop_cost_defaults.
        sa.Column("cogs_per_unit",          sa.Numeric(10, 2), nullable=True),
        sa.Column("shipping_cost_per_unit", sa.Numeric(10, 2), nullable=True),

        sa.Column("currency", sa.String(8), nullable=True),

        # Provenance tracking so the Settings UI can show "imported from
        # CSV" vs "entered manually" vs "auto-pulled from Shopify Admin".
        sa.Column("source", sa.String(32), nullable=False,
                  server_default=sa.text("'manual'")),

        sa.Column("created_at", sa.DateTime(timezone=False),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=False),
                  nullable=False, server_default=sa.text("NOW()")),

        sa.UniqueConstraint(
            "shop_domain", "product_key",
            name="uq_product_costs_shop_product",
        ),
    )

    # Composite index optimizes pnl_engine's join-by-shop-and-product lookup.
    op.create_index(
        "ix_product_costs_shop_product",
        "product_costs",
        ["shop_domain", "product_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_costs_shop_product", table_name="product_costs")
    op.drop_table("product_costs")
    op.drop_table("shop_cost_defaults")
