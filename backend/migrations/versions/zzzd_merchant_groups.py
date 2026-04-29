"""merchant_groups — Gap #5 multi-store consolidation (Lite-flipped)

Revision ID: zzzd_merchant_groups
Revises: zzzc_inventory_snapshots
Create Date: 2026-04-29

TIER_2 — formalizes two tables that were already created at runtime
by `Base.metadata.create_all` (anti-pattern). Without this migration
a fresh deploy would have no schema for these endpoints. Per the
$0-60 parity doctrine the feature flips from Pro to Lite in the same
turn — Putler $29 ships multi-store; we ship at $39 + better quality.

What this migration does
------------------------
1. Idempotently creates `merchant_groups` and `merchant_group_members`
   if they do not already exist (covers both fresh deploys and the
   already-existing dev DB).
2. Adds a partial-unique index on `(group_id) WHERE is_primary` so
   only ONE shop per group can be flagged primary. Closes the
   add_member race that prior service-layer code couldn't fully
   prevent.

Notes
-----
- We use `IF NOT EXISTS` for the CREATE TABLE so this migration
  does not fail when run against the dev DB where create_all already
  produced the tables.
- The unique partial index is the durable race guard; the service
  layer flips other rows to is_primary=False atomically before insert
  but a second concurrent transaction could still slip past Python
  serialization. The DB index is the final word.
"""
from __future__ import annotations

from alembic import op


revision = "zzzd_merchant_groups"
down_revision = "zzzc_inventory_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_groups (
            id            SERIAL PRIMARY KEY,
            name          VARCHAR(200) NOT NULL,
            owner_email   VARCHAR NOT NULL,
            description   VARCHAR(500),
            base_currency VARCHAR(8) NOT NULL DEFAULT 'EUR',
            created_at    TIMESTAMP NOT NULL DEFAULT now(),
            updated_at    TIMESTAMP NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_groups_owner_email ON merchant_groups (owner_email);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_group_members (
            id          SERIAL PRIMARY KEY,
            group_id    INTEGER NOT NULL REFERENCES merchant_groups(id) ON DELETE CASCADE,
            shop_domain VARCHAR NOT NULL,
            label       VARCHAR(120),
            is_primary  BOOLEAN NOT NULL DEFAULT FALSE,
            added_at    TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT uq_mgm_group_shop UNIQUE (group_id, shop_domain)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_group_members_group_id ON merchant_group_members (group_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_group_members_shop_domain ON merchant_group_members (shop_domain);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mgm_shop ON merchant_group_members (shop_domain);"
    )
    # Partial unique index — race-proof primary-uniqueness invariant.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mgm_one_primary_per_group
        ON merchant_group_members (group_id)
        WHERE is_primary IS TRUE;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_mgm_one_primary_per_group;")
    op.execute("DROP TABLE IF EXISTS merchant_group_members;")
    op.execute("DROP TABLE IF EXISTS merchant_groups;")
