"""Create ops_alerts table for internal operational alerting.

Durable alert records for webhook drift, GDPR failures, and other
operationally significant events. Read by operators and future AI agents.

Revision ID: ww1_ops_alerts
Revises: vv1_audit_log
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "ww1_ops_alerts"
down_revision = "vv1_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=True),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ops_alerts_severity_created", "ops_alerts", ["severity", "created_at"])
    op.create_index("ix_ops_alerts_unresolved", "ops_alerts", ["resolved", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_alerts_unresolved")
    op.drop_index("ix_ops_alerts_severity_created")
    op.drop_table("ops_alerts")
