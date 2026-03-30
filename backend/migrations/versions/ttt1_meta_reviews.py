"""Create meta_reviews table for system-level strategic prioritization.

Weekly meta-review stores structured JSON output from Opus that ranks
pending proposals, detects conflicts, and provides budget/focus guidance.

Revision ID: ttt1_meta_reviews
Revises: sss1_bugfix_outcome_tracking
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "ttt1_meta_reviews"
down_revision = "sss1_bugfix_outcome_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_reviews",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("review_window", sa.String(32), nullable=False),      # e.g. "2026-W13"
        sa.Column("status", sa.String(16), nullable=False),             # completed | skipped
        sa.Column("skipped_reason", sa.String(256), nullable=True),
        sa.Column("review_json", sa.Text, nullable=True),               # full structured output
        sa.Column("proposals_evaluated", sa.Integer, nullable=True),
        sa.Column("model_used", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_meta_reviews_window", "meta_reviews",
        ["review_window"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_meta_reviews_window")
    op.drop_table("meta_reviews")
