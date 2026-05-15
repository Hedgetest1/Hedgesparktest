"""create audit_chain_anchor singleton table for DB-anchor of chain head

Born 2026-05-15 (10k-structural sprint TIER_2 fresh approval). Closes
the threat model in `project_status_snapshot.md` "Audit chain DB
persistence":

  An attacker with BOTH Redis write + audit_log write access could
  wipe audit_log + the Redis chain-head key, then write a new chain
  from genesis. Verification would pass — Redis cross-check returns
  None when the key is missing (head_matches_redis=None, treated as
  "no anchor to verify").

Fix: persist the chain head to a dedicated singleton DB table.
On verify: compare last computed chain hash to anchor.chain_head.
Tampering signatures:

  - anchor.chain_head exists but audit_log is empty → table wipe
  - anchor.chain_head != computed → middle-row delete or modification
  - anchor table itself is dropped → catastrophic but visible

The anchor is updated inside the existing pg_advisory_xact_lock in
write_audit_log so the chain head + anchor stay consistent across
workers under concurrent writes.

Singleton design: PK constrained to 1; row is upserted on every
audit write. INT updated_at_revision counter increments to detect
read-modify-write races (paranoia).

Revision ID: zzzh_audit_chain_anchor
Revises: aa7_brain_immutability_hash, zzzg_plan_lite_canonical
Create Date: 2026-05-15

Merge note
----------
At the time this migration landed, the alembic chain had TWO heads:
  - aa7_brain_immutability_hash (the rev applied to prod)
  - zzzg_plan_lite_canonical    (a parallel branch never applied to prod)
This migration serves both purposes:
  1. Add audit_chain_anchor table (the substantive change)
  2. Merge the two heads so future migrations have a single tip
The down_revision tuple includes both heads so alembic treats this as
a merge migration.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "zzzh_audit_chain_anchor"
down_revision = ("aa7_brain_immutability_hash", "zzzg_plan_lite_canonical")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_chain_anchor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_head", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "revision_counter",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Enforce singleton — only one row ever, PK=1
        sa.CheckConstraint("id = 1", name="audit_chain_anchor_singleton"),
    )

    # Seed the singleton row with genesis hash. On first audit write,
    # write_audit_log will UPSERT the actual head.
    op.execute(
        "INSERT INTO audit_chain_anchor (id, chain_head, revision_counter) "
        "VALUES (1, '" + ("0" * 64) + "', 0) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("audit_chain_anchor")
