"""Holdout enforcement: leakage detection + enforcement mode.

Adds leakage_suspected flag to execution_tracking for contamination detection.
Adds enforcement_mode to execution_opportunities (email|onsite|unknown).
"""

from alembic import op
import sqlalchemy as sa

revision = "rr1_holdout_enforcement"
down_revision = "qq1_holdout_counterfactual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execution_tracking",
        sa.Column("leakage_suspected", sa.Boolean, nullable=False, server_default="false"))

    op.add_column("execution_opportunities",
        sa.Column("enforcement_mode", sa.String(16), nullable=False, server_default="unknown"))
    # unknown = not yet determined, email = strict enforcement, onsite = best-effort


def downgrade() -> None:
    op.drop_column("execution_opportunities", "enforcement_mode")
    op.drop_column("execution_tracking", "leakage_suspected")
