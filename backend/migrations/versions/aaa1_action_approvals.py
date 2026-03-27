"""Create action_approvals table for TIER_1 human-gated execution.

Revision ID: aaa1_action_approvals
Revises: zz1_merchant_onboarding_status
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "aaa1_action_approvals"
down_revision = "zz1_merchant_onboarding_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("audit_log_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(256), nullable=True),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_action_approvals_audit_log_id", "action_approvals", ["audit_log_id"])
    op.create_index("ix_action_approvals_status", "action_approvals", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_action_approvals_status")
    op.drop_index("ix_action_approvals_audit_log_id")
    op.drop_table("action_approvals")
