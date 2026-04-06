"""Close the evolution-proposal learning loop: track apply + outcome.

Adds six columns to evolution_proposals so strategic proposals carry the
same outcome metadata that bugfix_candidates already have. This is the
minimum necessary to enable Monthly Opus to LEARN from reality instead
of re-pitching the same ideas every cycle.

Columns added
-------------
  linked_bugfix_candidate_id  — FK to bugfix_candidates.id; set when a
                                proposal is converted/adopted by the
                                bugfix pipeline. Enables outcome lookup.
  applied_at                  — timestamp at which the proposal's code
                                change landed in main (mirrors
                                bugfix_candidates.applied_at).
  applied_commit_sha          — git SHA of the implementing commit.
  outcome_status              — effective | ineffective | inconclusive
                                | pending. NULL until measurement runs.
  outcome_measured_at         — timestamp when the outcome was recorded.
  outcome_evidence            — JSON: {source:'bugfix_candidate', bugfix_id,
                                window_hours, alerts_before, alerts_after, ...}
                                Copied from the linked bugfix's evidence.

Revision ID: hhh1_evolution_proposal_outcomes
Revises: ggg2_remediation_class
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "hhh1_evolution_proposal_outcomes"
down_revision = "ggg2_remediation_class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evolution_proposals",
        sa.Column("linked_bugfix_candidate_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("applied_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("applied_commit_sha", sa.String(64), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("outcome_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("outcome_measured_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "evolution_proposals",
        sa.Column("outcome_evidence", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_evolution_proposals_outcome",
        "evolution_proposals",
        ["outcome_status", "outcome_measured_at"],
    )
    op.create_index(
        "ix_evolution_proposals_linked_bugfix",
        "evolution_proposals",
        ["linked_bugfix_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_proposals_linked_bugfix", table_name="evolution_proposals")
    op.drop_index("ix_evolution_proposals_outcome", table_name="evolution_proposals")
    op.drop_column("evolution_proposals", "outcome_evidence")
    op.drop_column("evolution_proposals", "outcome_measured_at")
    op.drop_column("evolution_proposals", "outcome_status")
    op.drop_column("evolution_proposals", "applied_commit_sha")
    op.drop_column("evolution_proposals", "applied_at")
    op.drop_column("evolution_proposals", "linked_bugfix_candidate_id")
