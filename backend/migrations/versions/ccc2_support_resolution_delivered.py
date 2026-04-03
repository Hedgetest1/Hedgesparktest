"""add resolution_delivered_at to support_incidents

Revision ID: ccc2_support_resolution_delivered
Revises: bbb2_sentry_incidents
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "ccc2_support_resolution_delivered"
down_revision = "bbb2_sentry_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_incidents", sa.Column("resolution_delivered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("support_incidents", "resolution_delivered_at")
