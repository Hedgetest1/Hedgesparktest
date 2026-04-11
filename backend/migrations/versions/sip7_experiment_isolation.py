"""experiment isolation — bootstrap flag on autonomous_actions + active_nudges

Revision ID: sip7_experiment_isolation
Revises: sip6_share_and_intelligence
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = "sip7_experiment_isolation"
down_revision = "sip6_share_and_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flag for manually-forced experiments (threshold overrides, manual deploys)
    # These are excluded from SIP learning and CIG aggregation
    op.add_column("autonomous_actions",
                  sa.Column("is_bootstrap", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("active_nudges",
                  sa.Column("is_bootstrap", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("active_nudges", "is_bootstrap")
    op.drop_column("autonomous_actions", "is_bootstrap")
