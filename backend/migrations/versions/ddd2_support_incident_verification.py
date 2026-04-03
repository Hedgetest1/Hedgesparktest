"""add resolution_verified and fix_outcome to support_incidents

Revision ID: ddd2_support_incident_verification
Revises: ccc2_support_resolution_delivered
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "ddd2_support_incident_verification"
down_revision = "ccc2_support_resolution_delivered"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_incidents", sa.Column("resolution_verified", sa.Boolean(), nullable=True))
    op.add_column("support_incidents", sa.Column("fix_outcome", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("support_incidents", "fix_outcome")
    op.drop_column("support_incidents", "resolution_verified")
