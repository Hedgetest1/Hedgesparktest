"""Final-pass additions for self-driving evolution loop.

Three nullable columns — all optional, all additive, zero backward-compat risk.

  affected_files               JSON array of files touched by the
                               applied_commit_sha. Populated at rollback-
                               decision time (git show --name-only). Drives
                               full-blast-radius TIER_2 safety check.
  linked_nudge_ids             JSON array of active_nudges.id values this
                               proposal modifies. When populated, enables
                               true causal attribution via exposed-vs-
                               holdout comparison on nudge_events.
  extended_from_proposal_id    FK-ish to parent evolution_proposals.id
                               when this proposal was auto-generated as a
                               deeper variant of a winning proposal (by the
                               auto-extend loop).

Revision ID: kkk1_evolution_final_pass
Revises: jjj1_evolution_decision_and_rollback
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "kkk1_evolution_final_pass"
down_revision = "jjj1_evolution_decision_and_rollback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("affected_files", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("linked_nudge_ids", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("extended_from_proposal_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_evolution_proposals_extended_from",
        "evolution_proposals",
        ["extended_from_proposal_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_proposals_extended_from", table_name="evolution_proposals")
    op.drop_column("evolution_proposals", "extended_from_proposal_id")
    op.drop_column("evolution_proposals", "linked_nudge_ids")
    op.drop_column("evolution_proposals", "affected_files")
