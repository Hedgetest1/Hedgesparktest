"""Add is_synthetic flag to merchants table.

Synthetic merchants are created by the simulation engine for operational
hardening. They are permanently excluded from real_merchant evidence
classification and product learning.

Revision ID: qqq1_synthetic_merchant_flag
Revises: ppp1_learning_isolation
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "qqq1_synthetic_merchant_flag"
down_revision = "ppp1_learning_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column(
            "is_synthetic",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("merchants", "is_synthetic")
