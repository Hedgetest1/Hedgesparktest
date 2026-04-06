"""Add strategy alignment tracking to evolution_proposals.

Two additive columns that bind every bet to the declared North Star.

  strategy_alignment_score   FLOAT 0–10; computed at parse time from the
                             bet's text against the Tier-1/2/3 strategy
                             vocabulary. Bets below the threshold are
                             REJECTED — good ideas outside strategy lose
                             to strategic focus.
  strategy_version           INTEGER; records which strategy this bet
                             was evaluated against, so bets stay tied to
                             the strategy in force at the time they were
                             created.

Revision ID: nnn1_evolution_strategy_alignment
Revises: mmm1_evolution_ux_governance
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "nnn1_evolution_strategy_alignment"
down_revision = "mmm1_evolution_ux_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("strategy_alignment_score", sa.Float(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("strategy_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("evolution_proposals", "strategy_version")
    op.drop_column("evolution_proposals", "strategy_alignment_score")
