"""Add delivery tracking columns to ops_alerts.

Tracks whether external alert delivery (Slack) was attempted,
succeeded, or failed. DB persistence remains the source of truth —
these columns are informational only.

Revision ID: xx1_alert_delivery_tracking
Revises: ww1_ops_alerts
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "xx1_alert_delivery_tracking"
down_revision = "ww1_ops_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ops_alerts", sa.Column("delivered_at", sa.DateTime(), nullable=True))
    op.add_column("ops_alerts", sa.Column("delivery_status", sa.String(16), nullable=True))
    op.add_column("ops_alerts", sa.Column("delivery_error", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("ops_alerts", "delivery_error")
    op.drop_column("ops_alerts", "delivery_status")
    op.drop_column("ops_alerts", "delivered_at")
