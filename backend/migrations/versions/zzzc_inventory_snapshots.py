"""inventory_snapshots — Gap #4 Inventory KPIs (Lite)

Revision ID: zzzc_inventory_snapshots
Revises: zzzb_merchant_saved_reports
Create Date: 2026-04-28

TIER_2 — explicit founder GO 2026-04-28 ("Stock health, non è male ;)
Procedi"). NO new OAuth scope; uses existing read_products which
already exposes variant.inventory_quantity. Multi-location is Pro/Scale
moat (future).

What this migration does
------------------------
1. New table `inventory_snapshots` — daily per-(shop, product, variant)
   snapshot rows fed by the aggregation_worker. Replaces no existing
   data.
2. New column `merchants.inventory_lead_time_days` — per-shop override
   for the 14-day default reorder-lead-time. NULL → use 14.

Storage estimate at 10k merchants × 50 SKUs × 90d retention ≈ 45M rows
(~5 GB). Retention task purges >90d.

Indexes
-------
- UNIQUE (shop_domain, product_url, COALESCE(variant_id,''), snapshot_date)
  → atomic upsert dedup, also covers "today's snapshot" lookup
- (shop_domain, snapshot_date DESC) WHERE deleted_at IS NULL
  → no longer applicable; snapshots are simple time-series rows
- (shop_domain, product_url, snapshot_date DESC) for the
  "current state per product" query (latest row per product) which is
  the dashboard's hot path
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzzc_inventory_snapshots"
down_revision: Union[str, Sequence[str], None] = "zzzb_merchant_saved_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. inventory_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("product_url", sa.String(), nullable=False),
        sa.Column("product_title", sa.String(), nullable=True),
        # Empty string ('') for product-level rows; concrete id for
        # per-variant rows. NOT NULL DEFAULT '' lets standard UNIQUE
        # treat both shapes deterministically — required because
        # Postgres standard UNIQUE treats NULLs as distinct, which
        # would let multiple "no-variant" rows coexist.
        sa.Column(
            "variant_id",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("inventory_quantity", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Atomic-upsert UNIQUE — real CONSTRAINT (not just an index) so
    # SQLAlchemy `pg_insert(...).on_conflict_do_update(constraint=...)`
    # can target it.
    op.create_unique_constraint(
        "uq_inventory_shop_product_variant_date",
        "inventory_snapshots",
        ["shop_domain", "product_url", "variant_id", "snapshot_date"],
    )
    op.create_index(
        "idx_inventory_shop_date",
        "inventory_snapshots",
        ["shop_domain", sa.text("snapshot_date DESC")],
    )
    op.create_index(
        "idx_inventory_shop_product_latest",
        "inventory_snapshots",
        ["shop_domain", "product_url", sa.text("snapshot_date DESC")],
    )

    # ------------------------------------------------------------------
    # 2. merchants — inventory_lead_time_days override
    # ------------------------------------------------------------------
    op.add_column(
        "merchants",
        sa.Column("inventory_lead_time_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "inventory_lead_time_days")
    op.drop_index("idx_inventory_shop_product_latest", table_name="inventory_snapshots")
    op.drop_index("idx_inventory_shop_date", table_name="inventory_snapshots")
    op.drop_constraint(
        "uq_inventory_shop_product_variant_date",
        "inventory_snapshots",
        type_="unique",
    )
    op.drop_table("inventory_snapshots")
