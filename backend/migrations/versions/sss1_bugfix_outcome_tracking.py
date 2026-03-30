"""Add outcome tracking columns to bugfix_candidates.

Enables closed-loop learning: after a bugfix is applied, measure whether
the original alert/error pattern stopped recurring.

outcome_status: effective | ineffective | inconclusive | pending
outcome_measured_at: timestamp of measurement

Safe: nullable columns, no data migration needed.

Revision ID: sss1_bugfix_outcome_tracking
Revises: rrr1_visitor_product_state_idx
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "sss1_bugfix_outcome_tracking"
down_revision = "rrr1_visitor_product_state_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bugfix_candidates", sa.Column("outcome_status", sa.String(32), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("outcome_measured_at", sa.DateTime, nullable=True))
    op.add_column("bugfix_candidates", sa.Column("outcome_evidence", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "outcome_evidence")
    op.drop_column("bugfix_candidates", "outcome_measured_at")
    op.drop_column("bugfix_candidates", "outcome_status")
