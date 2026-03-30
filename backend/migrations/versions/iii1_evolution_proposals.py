"""Create evolution_proposals table.

Revision ID: iii1_evolution_proposals
Revises: hhh1_merge_outcomes
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "iii1_evolution_proposals"
down_revision = "hhh1_merge_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("proposal_type", sa.String(32), nullable=False),
        sa.Column("target_file", sa.String(256), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expected_impact", sa.Text(), nullable=True),
        sa.Column("auto_applicable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("audit_cycle", sa.String(32), nullable=True),
        sa.Column("dedup_key", sa.String(256), nullable=True),
    )
    op.create_index("ix_evolution_proposals_status", "evolution_proposals", ["status", "created_at"])
    op.create_index("ix_evolution_proposals_dedup", "evolution_proposals", ["dedup_key"])


def downgrade() -> None:
    op.drop_index("ix_evolution_proposals_dedup")
    op.drop_index("ix_evolution_proposals_status")
    op.drop_table("evolution_proposals")
