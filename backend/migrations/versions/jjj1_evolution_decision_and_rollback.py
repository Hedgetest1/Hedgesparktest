"""Add decision engine + rollback tracking to evolution_proposals.

Six additive columns — the decision layer that turns "measured outcomes"
into "actions". Every field is nullable and every downstream action is
routed through existing safety gates (tier_check, bugfix approval).

Columns
-------
  confidence_score        FLOAT 0-1; Wilson/z-score-derived confidence
                          of the business_outcome classification.
  decision_status         observe | reinforce | extend_carefully |
                          rollback_proposed | rollback_blocked |
                          rollback_skipped | ignored | NULL
  decision_decided_at     timestamp of the decision
  rollback_candidate_id   FK-ish to bugfix_candidates.id — the
                          auto-generated reverse-patch candidate created
                          by the decision engine. NULL unless a rollback
                          was actually proposed.
  affected_shop_domains   JSON array of shop domains this proposal
                          impacts. NULL/empty = global.
  affected_product_urls   JSON array of product URLs this proposal
                          impacts. NULL/empty = global.

Revision ID: jjj1_evolution_decision_and_rollback
Revises: iii1_evolution_proposal_business_outcomes
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "jjj1_evolution_decision_and_rollback"
down_revision = "iii1_evolution_proposal_business_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("confidence_score", sa.Float(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("decision_status", sa.String(32), nullable=True))
    op.add_column("evolution_proposals", sa.Column("decision_decided_at", sa.DateTime(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("rollback_candidate_id", sa.Integer(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("affected_shop_domains", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("affected_product_urls", sa.Text(), nullable=True))
    op.create_index(
        "ix_evolution_proposals_decision",
        "evolution_proposals",
        ["decision_status", "decision_decided_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_proposals_decision", table_name="evolution_proposals")
    op.drop_column("evolution_proposals", "affected_product_urls")
    op.drop_column("evolution_proposals", "affected_shop_domains")
    op.drop_column("evolution_proposals", "rollback_candidate_id")
    op.drop_column("evolution_proposals", "decision_decided_at")
    op.drop_column("evolution_proposals", "decision_status")
    op.drop_column("evolution_proposals", "confidence_score")
