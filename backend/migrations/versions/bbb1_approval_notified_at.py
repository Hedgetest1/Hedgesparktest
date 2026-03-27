"""Add notified_at to action_approvals for Slack dedup.

Revision ID: bbb1_approval_notified_at
Revises: aaa1_action_approvals
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "bbb1_approval_notified_at"
down_revision = "aaa1_action_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("action_approvals", sa.Column("notified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("action_approvals", "notified_at")
