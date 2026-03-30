"""Create model_upgrade_proposals table.

Revision ID: jjj1_model_upgrade_proposals
Revises: iii1_evolution_proposals
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "jjj1_model_upgrade_proposals"
down_revision = "iii1_evolution_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_upgrade_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("current_provider", sa.String(32), nullable=False),
        sa.Column("current_model", sa.String(128), nullable=False),
        sa.Column("candidate_provider", sa.String(32), nullable=False),
        sa.Column("candidate_model", sa.String(128), nullable=False),
        sa.Column("target_module", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expected_benefit", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="LEVEL_2"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("eval_result", sa.String(16), nullable=True),
        sa.Column("eval_detail", sa.Text(), nullable=True),
        sa.Column("eval_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_model_upgrade_status", "model_upgrade_proposals", ["status", "created_at"])
    op.create_index("ix_model_upgrade_dedup", "model_upgrade_proposals", ["current_model", "candidate_model", "target_module"])


def downgrade() -> None:
    op.drop_index("ix_model_upgrade_dedup")
    op.drop_index("ix_model_upgrade_status")
    op.drop_table("model_upgrade_proposals")
