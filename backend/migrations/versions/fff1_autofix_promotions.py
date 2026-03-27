"""Create autofix_promotions table.

Revision ID: fff1_autofix_promotions
Revises: eee1_patch_risk_tier
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "fff1_autofix_promotions"
down_revision = "eee1_patch_risk_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autofix_promotions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bugfix_candidate_id", sa.Integer(), nullable=False),
        sa.Column("git_commit_sha", sa.String(64), nullable=False),
        sa.Column("branch_name", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("ci_url", sa.String(512), nullable=True),
        sa.Column("ci_result", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_autofix_promotions_bugfix", "autofix_promotions", ["bugfix_candidate_id"])
    op.create_index("ix_autofix_promotions_status", "autofix_promotions", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_autofix_promotions_status")
    op.drop_index("ix_autofix_promotions_bugfix")
    op.drop_table("autofix_promotions")
