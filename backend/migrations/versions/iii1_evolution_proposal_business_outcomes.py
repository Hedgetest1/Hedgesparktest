"""Add business-outcome columns to evolution_proposals.

The existing outcome_status column measures TECHNICAL impact (alert counts,
worker errors) via the bugfix 48h measurement. That tells us whether the
proposal fixed the bug — not whether it made money.

This migration adds parallel BUSINESS outcome tracking:

  business_outcome       improved | declined | stable | not_applicable
                         | inconclusive | pending
                         (NULL until measurement runs)
  business_measured_at   when the business window was evaluated
  business_evidence      JSON: {domain, window_days, before, after, control,
                                trend_adjusted_delta, sample_size, ...}

This is purely additive. No existing columns, indexes, or callers change.
The tech outcome loop continues to run exactly as before.

Revision ID: iii1_evolution_proposal_business_outcomes
Revises: hhh1_evolution_proposal_outcomes
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "iii1_evolution_proposal_business_outcomes"
down_revision = "hhh1_evolution_proposal_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evolution_proposals",
        sa.Column("business_outcome", sa.String(32), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("business_measured_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("business_evidence", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_evolution_proposals_business_outcome",
        "evolution_proposals",
        ["business_outcome", "business_measured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_proposals_business_outcome", table_name="evolution_proposals")
    op.drop_column("evolution_proposals", "business_evidence")
    op.drop_column("evolution_proposals", "business_measured_at")
    op.drop_column("evolution_proposals", "business_outcome")
