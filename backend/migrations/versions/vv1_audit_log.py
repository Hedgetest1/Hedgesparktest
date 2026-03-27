"""Create audit_log table for immutable action traceability.

Append-only by convention. Records every agent/system/admin action.
Foundation for future AI agent governance and compliance auditing.

Revision ID: vv1_audit_log
Revises: uu1_signal_confidence
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "vv1_audit_log"
down_revision = "uu1_signal_confidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_name", sa.String(128), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.String(256), nullable=True),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("before_state", sa.Text(), nullable=True),
        sa.Column("after_state", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("approval_mode", sa.String(32), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_log_shop_created", "audit_log", ["shop_domain", "created_at"])
    op.create_index("ix_audit_log_action_type", "audit_log", ["action_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_action_type")
    op.drop_index("ix_audit_log_shop_created")
    op.drop_table("audit_log")
