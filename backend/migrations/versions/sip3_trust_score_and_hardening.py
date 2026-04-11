"""add trust_score, autonomous_paused, nudge_type_cooldowns to SIP

Revision ID: sip3_trust_score_and_hardening
Revises: sip2_autonomous_actions
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip3_trust_score_and_hardening"
down_revision = "sip2_autonomous_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("store_intelligence_profiles",
                  sa.Column("trust_score", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("autonomous_paused", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("pause_reason", sa.String(256), nullable=True))
    op.add_column("store_intelligence_profiles",
                  sa.Column("nudge_type_cooldowns", postgresql.JSONB, nullable=True))
    op.add_column("store_intelligence_profiles",
                  sa.Column("total_autonomous_actions", sa.Integer, nullable=False, server_default="0"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("total_positive_outcomes", sa.Integer, nullable=False, server_default="0"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("total_rollbacks", sa.Integer, nullable=False, server_default="0"))
    op.add_column("store_intelligence_profiles",
                  sa.Column("last_outcome_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("store_intelligence_profiles", "last_outcome_at")
    op.drop_column("store_intelligence_profiles", "total_rollbacks")
    op.drop_column("store_intelligence_profiles", "total_positive_outcomes")
    op.drop_column("store_intelligence_profiles", "total_autonomous_actions")
    op.drop_column("store_intelligence_profiles", "nudge_type_cooldowns")
    op.drop_column("store_intelligence_profiles", "pause_reason")
    op.drop_column("store_intelligence_profiles", "autonomous_paused")
    op.drop_column("store_intelligence_profiles", "trust_score")
