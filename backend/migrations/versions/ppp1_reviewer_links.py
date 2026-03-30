"""Add reviewer_assessment_id to bugfix_candidates and evolution_proposals.

Links entities to their reviewer assessment for auditability.

Revision ID: ppp1_reviewer_links
Revises: ooo1_project_brain
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = "ppp1_reviewer_links"
down_revision = "ooo1_project_brain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bugfix_candidates",
                  sa.Column("reviewer_assessment_id", sa.Integer(), nullable=True))
    op.add_column("evolution_proposals",
                  sa.Column("reviewer_assessment_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("evolution_proposals", "reviewer_assessment_id")
    op.drop_column("bugfix_candidates", "reviewer_assessment_id")
