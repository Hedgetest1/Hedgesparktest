"""Create bugfix_candidates table for code-fix pipeline.

Revision ID: ccc1_bugfix_candidates
Revises: bbb1_approval_notified_at
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "ccc1_bugfix_candidates"
down_revision = "bbb1_approval_notified_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bugfix_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("patch_summary", sa.Text(), nullable=True),
        sa.Column("patch_diff", sa.Text(), nullable=True),
        sa.Column("patch_files", sa.Text(), nullable=True),
        sa.Column("test_command", sa.String(512), nullable=True),
        sa.Column("test_result", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_bugfix_candidates_status", "bugfix_candidates", ["status", "created_at"])
    op.create_index("ix_bugfix_candidates_source", "bugfix_candidates", ["source_type", "source_ref"])


def downgrade() -> None:
    op.drop_index("ix_bugfix_candidates_source")
    op.drop_index("ix_bugfix_candidates_status")
    op.drop_table("bugfix_candidates")
