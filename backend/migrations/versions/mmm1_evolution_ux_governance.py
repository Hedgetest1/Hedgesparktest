"""Add UX-governance columns to evolution_proposals.

Two columns close the last governance gap. Both additive, both nullable.

  ux_sensitive     True when a bet changes merchant-visible structure
                   (dashboard layout, KPI definitions, navigation,
                   notifications, merchant terminology). ux_sensitive=True
                   bets are REFUSED auto-conversion — always require human.
  impact_radius    internal | visible | structural — the blast radius of
                   the change. 'structural' forces ux_sensitive=True.

Revision ID: mmm1_evolution_ux_governance
Revises: lll1_evolution_strategic_bets
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "mmm1_evolution_ux_governance"
down_revision = "lll1_evolution_strategic_bets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("ux_sensitive", sa.Boolean(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("impact_radius", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("evolution_proposals", "impact_radius")
    op.drop_column("evolution_proposals", "ux_sensitive")
