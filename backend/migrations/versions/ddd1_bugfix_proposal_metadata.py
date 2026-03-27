"""Add proposal + apply metadata to bugfix_candidates.

Revision ID: ddd1_bugfix_proposal_metadata
Revises: ccc1_bugfix_candidates
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "ddd1_bugfix_proposal_metadata"
down_revision = "ccc1_bugfix_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bugfix_candidates", sa.Column("proposal_attempted_at", sa.DateTime(), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("proposal_error", sa.String(512), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("proposal_provider", sa.String(32), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("git_commit_sha", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "git_commit_sha")
    op.drop_column("bugfix_candidates", "proposal_provider")
    op.drop_column("bugfix_candidates", "proposal_error")
    op.drop_column("bugfix_candidates", "proposal_attempted_at")
