"""Add iteration_num to bugfix_candidates — Sprint C of CTO-brain
pipeline upgrade (iterative fix loop post-DA).

Revision ID: bbb3_bugfix_candidate_iteration_num
Revises: bbb2_adversarial_review_findings
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa

revision = "bbb3_bugfix_candidate_iteration_num"
down_revision = "bbb2_adversarial_review_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bugfix_candidates",
        sa.Column(
            "iteration_num",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_index(
        "ix_bugfix_candidates_iteration",
        "bugfix_candidates",
        ["parent_candidate_id", "iteration_num"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bugfix_candidates_iteration",
        table_name="bugfix_candidates",
    )
    op.drop_column("bugfix_candidates", "iteration_num")
