"""add session_version to merchants

Revision ID: ee1_session_version
Revises: dd1_events_composite_index
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = "ee1_session_version"
down_revision = "dd1_events_composite_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("merchants", "session_version")
