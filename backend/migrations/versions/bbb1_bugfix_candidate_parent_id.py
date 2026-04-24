"""Add parent_candidate_id FK to bugfix_candidates — enables sibling-hunt
pipeline phase (Sprint A from project_cto_brain_pipeline_gap.md).

When the pipeline applies a fix and runs sibling_hunt, each matching
pattern-hit creates a new BugFixCandidate with this FK pointing at
the original ("parent") candidate. The tree lets operators/LLM trace
which patches form a batch-fix-class.

Revision ID: bbb1_bugfix_candidate_parent_id
Revises: aaa4_bugfix_candidate_proposal_model
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "bbb1_bugfix_candidate_parent_id"
down_revision = "aaa4_bugfix_candidate_proposal_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bugfix_candidates",
        sa.Column("parent_candidate_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bugfix_candidates_parent",
        "bugfix_candidates",
        "bugfix_candidates",
        ["parent_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bugfix_candidates_parent",
        "bugfix_candidates",
        ["parent_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bugfix_candidates_parent", table_name="bugfix_candidates")
    op.drop_constraint(
        "fk_bugfix_candidates_parent",
        "bugfix_candidates",
        type_="foreignkey",
    )
    op.drop_column("bugfix_candidates", "parent_candidate_id")
