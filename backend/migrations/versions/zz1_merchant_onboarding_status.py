"""Add onboarding_status column to merchants.

Tracks the automated onboarding state machine:
  pending → configuring → ready | failed

Revision ID: zz1_merchant_onboarding_status
Revises: yy1_action_outcomes
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zz1_merchant_onboarding_status"
down_revision = "yy1_action_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("onboarding_status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.add_column(
        "merchants",
        sa.Column("onboarding_error", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "onboarding_error")
    op.drop_column("merchants", "onboarding_status")
