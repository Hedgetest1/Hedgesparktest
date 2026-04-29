"""rename merchants.plan value 'starter' → 'lite'

Founder mandate 2026-04-29: "non voglio piu vedere starter da
nessuna cazzo di parte". The 2026-04-23 Lite→Starter rename was
reverted but the DB plan column kept "starter" as the canonical
value. This migration flips every existing row + the column
default so the value layer matches the user-facing copy.

Atomic: backend is restarted around this migration so there's no
in-flight read with stale "starter" expectation. Pre-merchant
footprint (~2 rows in prod), so downtime is negligible.

Revision ID: zzzg_rename_plan_starter_to_lite
Revises: zzzf_google_oauth_tokens
Create Date: 2026-04-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "zzzg_rename_plan_starter_to_lite"
down_revision = "zzzf_google_oauth_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flip every existing "starter" row to "lite". Idempotent —
    # safe to re-run; affects only rows still on the old value.
    op.execute(
        "UPDATE merchants SET plan = 'lite' WHERE plan = 'starter'"
    )

    # Change the column default from "starter" to "lite" so new
    # signups land on the renamed value without needing a code
    # round-trip. ALTER COLUMN ... SET DEFAULT is online on
    # Postgres (no table rewrite, no row lock beyond the catalog).
    op.alter_column(
        "merchants",
        "plan",
        server_default="lite",
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Reverse: restore "starter" default + flip rows back. Only
    # makes sense if a rollback to pre-rename code is also needed.
    op.alter_column(
        "merchants",
        "plan",
        server_default="starter",
        existing_type=sa.String(),
        existing_nullable=False,
    )
    op.execute(
        "UPDATE merchants SET plan = 'starter' WHERE plan = 'lite'"
    )
