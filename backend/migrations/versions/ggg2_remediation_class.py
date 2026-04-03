"""add remediation_class to bugfix_candidates

Revision ID: ggg2_remediation_class
Revises: fff2_autonomous_scoring
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "ggg2_remediation_class"
down_revision = "fff2_autonomous_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bugfix_candidates", sa.Column("remediation_class", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "remediation_class")
