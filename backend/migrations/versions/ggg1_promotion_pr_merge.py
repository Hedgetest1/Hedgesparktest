"""Add PR + merge + remote CI fields to autofix_promotions.

Revision ID: ggg1_promotion_pr_merge
Revises: fff1_autofix_promotions
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "ggg1_promotion_pr_merge"
down_revision = "fff1_autofix_promotions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("autofix_promotions", sa.Column("pr_url", sa.String(512), nullable=True))
    op.add_column("autofix_promotions", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("autofix_promotions", sa.Column("merged_at", sa.DateTime(), nullable=True))
    op.add_column("autofix_promotions", sa.Column("merge_commit_sha", sa.String(64), nullable=True))
    op.add_column("autofix_promotions", sa.Column("remote_ci_status", sa.String(32), nullable=True))
    op.add_column("autofix_promotions", sa.Column("remote_ci_url", sa.String(512), nullable=True))
    op.add_column("autofix_promotions", sa.Column("remote_ci_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("autofix_promotions", "remote_ci_checked_at")
    op.drop_column("autofix_promotions", "remote_ci_url")
    op.drop_column("autofix_promotions", "remote_ci_status")
    op.drop_column("autofix_promotions", "merge_commit_sha")
    op.drop_column("autofix_promotions", "merged_at")
    op.drop_column("autofix_promotions", "pr_number")
    op.drop_column("autofix_promotions", "pr_url")
