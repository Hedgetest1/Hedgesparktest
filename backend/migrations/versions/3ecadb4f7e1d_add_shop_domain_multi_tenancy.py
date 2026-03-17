"""add shop_domain multi-tenancy to all tables

Revision ID: 3ecadb4f7e1d
Revises: aefbbe8acc06
Create Date: 2026-03-17

Adds shop_domain to all 10 data tables using the three-step pattern:
  1. Add column as nullable
  2. Backfill existing rows with 'legacy.myshopify.com'
  3. Set NOT NULL

For tables that had a single-column UNIQUE on product_url, the existing
constraint is dropped first and replaced with a composite UNIQUE on
(shop_domain, product_url) after the column is fully populated.

Real PostgreSQL constraint names are used throughout — never assumed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision: str = "3ecadb4f7e1d"
down_revision: Union[str, None] = "aefbbe8acc06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_SHOP = "legacy.myshopify.com"

# ---------------------------------------------------------------------------
# GROUP A: tables with existing single-column UNIQUE on product_url.
# Real constraint names verified from live DB on 2026-03-17.
# Pattern: drop existing unique → add column → backfill → NOT NULL →
#          composite unique (shop_domain, product_url)
# ---------------------------------------------------------------------------
GROUP_A = [
    {
        "table": "market_lookup",
        "old_unique": "market_lookup_product_url_key",
        "new_unique": "uq_market_lookup_shop_domain_product_url",
    },
    {
        "table": "price_intelligence",
        "old_unique": "price_intelligence_product_url_key",
        "new_unique": "uq_price_intelligence_shop_domain_product_url",
    },
    {
        "table": "product_opportunities",
        "old_unique": "product_opportunities_product_url_key",
        "new_unique": "uq_product_opportunities_shop_domain_product_url",
    },
    {
        "table": "unique_product_detection",
        "old_unique": "unique_product_detection_product_url_key",
        "new_unique": "uq_unique_product_detection_shop_domain_product_url",
    },
]

# ---------------------------------------------------------------------------
# GROUP B: tables with no unique constraint on a tenant-scoped column.
# Pattern: add column → backfill → NOT NULL
# ---------------------------------------------------------------------------
GROUP_B = [
    "events",
    "price_watch",
    "products",
    "visitor_product_state",
    "visitors",
    "wishlist_items",
]


def upgrade() -> None:
    # --- GROUP A ---
    for entry in GROUP_A:
        table = entry["table"]
        old_unique = entry["old_unique"]
        new_unique = entry["new_unique"]

        # Step 0: drop the existing single-column unique constraint
        op.drop_constraint(old_unique, table, type_="unique")

        # Step 1: add shop_domain as nullable
        op.add_column(
            table,
            sa.Column("shop_domain", sa.String(), nullable=True),
        )

        # Step 2: backfill all existing rows
        op.execute(
            f"UPDATE {table} SET shop_domain = '{LEGACY_SHOP}' WHERE shop_domain IS NULL"
        )

        # Step 3: set NOT NULL
        op.alter_column(table, "shop_domain", nullable=False)

        # Step 4: add composite unique constraint
        op.create_unique_constraint(
            new_unique,
            table,
            ["shop_domain", "product_url"],
        )

    # --- GROUP B ---
    for table in GROUP_B:
        # Step 1: add shop_domain as nullable
        op.add_column(
            table,
            sa.Column("shop_domain", sa.String(), nullable=True),
        )

        # Step 2: backfill all existing rows
        op.execute(
            f"UPDATE {table} SET shop_domain = '{LEGACY_SHOP}' WHERE shop_domain IS NULL"
        )

        # Step 3: set NOT NULL
        op.alter_column(table, "shop_domain", nullable=False)


def downgrade() -> None:
    # --- GROUP B (reverse) ---
    for table in reversed(GROUP_B):
        op.drop_column(table, "shop_domain")

    # --- GROUP A (reverse) ---
    for entry in reversed(GROUP_A):
        table = entry["table"]
        old_unique = entry["old_unique"]
        new_unique = entry["new_unique"]

        # Drop composite unique
        op.drop_constraint(new_unique, table, type_="unique")

        # Drop shop_domain column
        op.drop_column(table, "shop_domain")

        # Restore original single-column unique on product_url
        op.create_unique_constraint(old_unique, table, ["product_url"])
