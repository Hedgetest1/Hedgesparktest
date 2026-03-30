"""Create system_snapshots and scaling_recommendations tables.

Revision ID: mmm1_scaling_intelligence
Revises: lll1_support_incidents
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "mmm1_scaling_intelligence"
down_revision = "lll1_support_incidents"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("date_bucket", sa.Date, nullable=False, unique=True),
        sa.Column("active_merchants", sa.Integer, nullable=False, server_default="0"),
        sa.Column("billing_active_merchants", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_events_24h", sa.Integer, nullable=True),
        sa.Column("llm_calls_24h", sa.Integer, nullable=True, server_default="0"),
        sa.Column("llm_estimated_cost_eur", sa.Float, nullable=True, server_default="0"),
        sa.Column("worker_error_rate", sa.Float, nullable=True, server_default="0"),
        sa.Column("cpu_pct", sa.Float, nullable=True),
        sa.Column("ram_used_mb", sa.Float, nullable=True),
        sa.Column("ram_total_mb", sa.Float, nullable=True),
        sa.Column("disk_used_pct", sa.Float, nullable=True),
        sa.Column("api_warning_count", sa.Integer, nullable=True, server_default="0"),
        sa.Column("support_incident_count", sa.Integer, nullable=True, server_default="0"),
        sa.Column("ops_alert_count", sa.Integer, nullable=True, server_default="0"),
    )

    op.create_table(
        "scaling_recommendations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("current_value", sa.String(128), nullable=True),
        sa.Column("projected_value", sa.String(128), nullable=True),
        sa.Column("projected_horizon_days", sa.Integer, nullable=True, server_default="30"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="low"),
        sa.Column("estimated_cost_increase_eur", sa.Float, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("acknowledged_by", sa.String(128), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime, nullable=True),
        sa.Column("dedup_key", sa.String(128), nullable=True, unique=True),
    )
    op.create_index("ix_scaling_rec_status_created", "scaling_recommendations", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_scaling_rec_status_created")
    op.drop_table("scaling_recommendations")
    op.drop_table("system_snapshots")
