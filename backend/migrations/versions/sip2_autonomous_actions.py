"""autonomous actions — system of record for autonomous revenue loop

Revision ID: sip2_autonomous_actions
Revises: sip1_store_intelligence_profiles
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip2_autonomous_actions"
down_revision = "sip1_store_intelligence_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autonomous_actions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, index=True),
        sa.Column("signal_type", sa.String, nullable=False),
        sa.Column("product_url", sa.String, nullable=False),
        sa.Column("nudge_id", sa.Integer, nullable=True),
        sa.Column("action_type", sa.String, nullable=False),
        sa.Column("nudge_type", sa.String, nullable=True),
        sa.Column("risk_level", sa.String(8), nullable=False),
        sa.Column("decision_reason", sa.Text, nullable=False),
        sa.Column("sip_confidence", sa.String(8), nullable=True),
        sa.Column("sip_nudge_score", sa.Float, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed", index=True),
        sa.Column("deployed_at", sa.DateTime, nullable=True),
        sa.Column("holdout_pct", sa.Integer, nullable=True),
        sa.Column("measurement_start", sa.DateTime, nullable=True),
        sa.Column("measurement_end", sa.DateTime, nullable=True),
        sa.Column("treatment_cvr", sa.Float, nullable=True),
        sa.Column("control_cvr", sa.Float, nullable=True),
        sa.Column("lift_pct", sa.Float, nullable=True),
        sa.Column("p_value", sa.Float, nullable=True),
        sa.Column("visitors_measured", sa.Integer, nullable=True),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("rollback_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("autonomous_actions")
