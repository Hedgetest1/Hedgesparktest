"""Strategic-bets redesign: decision memory + cost awareness on evolution_proposals.

Transforms Monthly Opus from "idea generator" into "CTO making decisions under
constraint". Each strategic bet now carries:

  revenue_thesis         concrete mechanism for why this moves revenue
  rejected_alternatives  JSON array of alternatives considered + why rejected
                         (decision memory — record WHY, not just what)
  infra_cost_estimate    none | small | medium | large — cost awareness gate
  exploration_bet        True if this bet is explicit exploration outside
                         the top-reinforced domains (enforces 20% floor)

All four columns are nullable + additive. No existing callers change.

Revision ID: lll1_evolution_strategic_bets
Revises: kkk1_evolution_final_pass
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "lll1_evolution_strategic_bets"
down_revision = "kkk1_evolution_final_pass"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("revenue_thesis", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("rejected_alternatives", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("infra_cost_estimate", sa.String(16), nullable=True))
    op.add_column("evolution_proposals", sa.Column("exploration_bet", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("evolution_proposals", "exploration_bet")
    op.drop_column("evolution_proposals", "infra_cost_estimate")
    op.drop_column("evolution_proposals", "rejected_alternatives")
    op.drop_column("evolution_proposals", "revenue_thesis")
