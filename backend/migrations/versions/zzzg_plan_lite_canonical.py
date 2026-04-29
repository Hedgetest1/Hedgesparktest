"""coerce merchants.plan default + non-Pro values to canonical 'lite'

Founder mandate 2026-04-29: 'lite' is the canonical entry-tier value
across DB + code. This migration:

  1. Coerces any existing non-Pro row to 'lite' (idempotent — runs
     once on the existing DB, no-op on fresh DBs because the
     create-table migration above already defaults to 'lite').
  2. Sets the column server_default to 'lite' so new merchants land
     on the canonical value at INSERT time without a code round-trip.

Pre-merchant footprint (~4 rows in prod), single-process backend
restarted around the migration so there's no in-flight read with a
stale tier expectation.

Revision ID: zzzg_plan_lite_canonical
Revises: zzzf_google_oauth_tokens
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "zzzg_plan_lite_canonical"
down_revision = "zzzf_google_oauth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Coerce any non-Pro row to the canonical entry-tier value.
    op.execute(
        "UPDATE merchants SET plan = 'lite' WHERE plan <> 'pro'"
    )

    # Set the column default. ALTER COLUMN ... SET DEFAULT is online
    # on Postgres (no table rewrite, no row lock beyond the catalog).
    op.alter_column(
        "merchants",
        "plan",
        server_default="lite",
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Symmetric reversal: drop the default and leave plan values
    # in place. Pre-merchants there is no historical legacy value
    # to restore — downgrade is identity-by-design.
    op.alter_column(
        "merchants",
        "plan",
        server_default=None,
        existing_type=sa.String(),
        existing_nullable=False,
    )
