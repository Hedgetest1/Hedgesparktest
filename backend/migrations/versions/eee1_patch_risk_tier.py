"""Add patch_risk_tier to bugfix_candidates.

Revision ID: eee1_patch_risk_tier
Revises: ddd1_bugfix_proposal_metadata
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "eee1_patch_risk_tier"
down_revision = "ddd1_bugfix_proposal_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bugfix_candidates", sa.Column("patch_risk_tier", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "patch_risk_tier")
