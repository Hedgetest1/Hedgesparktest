"""Create action_outcomes table for orchestrator outcome tracking.

Measures whether executed actions improved system state.
Links to audit_log by audit_log_id.

Revision ID: yy1_action_outcomes
Revises: xx1_alert_delivery_tracking
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "yy1_action_outcomes"
down_revision = "xx1_alert_delivery_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("audit_log_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(256), nullable=True),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("outcome_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_action_outcomes_audit_log_id", "action_outcomes", ["audit_log_id"])
    op.create_index("ix_action_outcomes_status", "action_outcomes", ["outcome_status", "executed_at"])


def downgrade() -> None:
    op.drop_index("ix_action_outcomes_status")
    op.drop_index("ix_action_outcomes_audit_log_id")
    op.drop_table("action_outcomes")
