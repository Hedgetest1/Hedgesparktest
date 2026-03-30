"""Create support_incidents table for merchant chatbot.

Revision ID: lll1_support_incidents
Revises: kkk1_active_model_configs
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "lll1_support_incidents"
down_revision = "kkk1_active_model_configs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "support_incidents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("shop_domain", sa.String(255), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="merchant_chat"),
        sa.Column("original_message", sa.Text, nullable=False),
        sa.Column("classification", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="low"),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("affected_area", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("linked_bugfix_candidate_id", sa.Integer, nullable=True),
        sa.Column("linked_ops_alert_id", sa.Integer, nullable=True),
        sa.Column("linked_evolution_proposal_id", sa.Integer, nullable=True),
        sa.Column("resolution_summary", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("response_text", sa.Text, nullable=True),
    )
    op.create_index("ix_support_incidents_status_created", "support_incidents", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_support_incidents_status_created")
    op.drop_table("support_incidents")
