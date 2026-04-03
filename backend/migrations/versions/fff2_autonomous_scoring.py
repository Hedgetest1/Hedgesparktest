"""add release tracking, priority/confidence scoring columns

Revision ID: fff2_autonomous_scoring
Revises: eee2_sentry_incident_hardening
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "fff2_autonomous_scoring"
down_revision = "eee2_sentry_incident_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SentryIncident: release tracking
    op.add_column("sentry_incidents", sa.Column("release", sa.String(128), nullable=True))
    op.add_column("sentry_incidents", sa.Column("is_regression_candidate", sa.String(8), nullable=True))

    # BugFixCandidate: priority scoring
    op.add_column("bugfix_candidates", sa.Column("priority_score", sa.Integer(), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("priority_detail", sa.Text(), nullable=True))

    # BugFixCandidate: confidence scoring
    op.add_column("bugfix_candidates", sa.Column("fix_confidence", sa.Integer(), nullable=True))
    op.add_column("bugfix_candidates", sa.Column("confidence_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("bugfix_candidates", "confidence_detail")
    op.drop_column("bugfix_candidates", "fix_confidence")
    op.drop_column("bugfix_candidates", "priority_detail")
    op.drop_column("bugfix_candidates", "priority_score")
    op.drop_column("sentry_incidents", "is_regression_candidate")
    op.drop_column("sentry_incidents", "release")
