"""adversarial resilience — trust profile, autonomy level, measurement health

Revision ID: sip4_adversarial_resilience
Revises: sip3_trust_score_and_hardening
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip4_adversarial_resilience"
down_revision = "sip3_trust_score_and_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Multi-dimensional trust profile replaces scalar trust_score
    op.add_column("store_intelligence_profiles",
                  sa.Column("trust_profile", postgresql.JSONB, nullable=True))
    # Autonomy level: 0-5
    op.add_column("store_intelligence_profiles",
                  sa.Column("autonomy_level", sa.Integer, nullable=False, server_default="0"))
    # Measurement health state: healthy / degraded / broken
    op.add_column("store_intelligence_profiles",
                  sa.Column("measurement_health", sa.String(16), nullable=False, server_default="healthy"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("measurement_health_detail", sa.String(512), nullable=True))
    # Nudge interaction matrix
    op.add_column("store_intelligence_profiles",
                  sa.Column("nudge_interaction_matrix", postgresql.JSONB, nullable=True))
    # Outcome contradiction count (same signal+nudge type, conflicting results)
    op.add_column("store_intelligence_profiles",
                  sa.Column("contradiction_count", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("store_intelligence_profiles", "contradiction_count")
    op.drop_column("store_intelligence_profiles", "nudge_interaction_matrix")
    op.drop_column("store_intelligence_profiles", "measurement_health_detail")
    op.drop_column("store_intelligence_profiles", "measurement_health")
    op.drop_column("store_intelligence_profiles", "autonomy_level")
    op.drop_column("store_intelligence_profiles", "trust_profile")
