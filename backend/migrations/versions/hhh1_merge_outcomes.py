"""Create merge_outcomes table.

Revision ID: hhh1_merge_outcomes
Revises: ggg1_promotion_pr_merge
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "hhh1_merge_outcomes"
down_revision = "ggg1_promotion_pr_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merge_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("bugfix_candidate_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("merge_commit_sha", sa.String(64), nullable=True),
        sa.Column("evaluation_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("evaluated_at", sa.DateTime(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_merge_outcomes_promotion", "merge_outcomes", ["promotion_id"])
    op.create_index("ix_merge_outcomes_status", "merge_outcomes", ["evaluation_status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_merge_outcomes_status")
    op.drop_index("ix_merge_outcomes_promotion")
    op.drop_table("merge_outcomes")
