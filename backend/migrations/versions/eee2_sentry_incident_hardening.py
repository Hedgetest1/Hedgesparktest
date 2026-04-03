"""add subsystem_class, merchant_impact, affected_shop to sentry_incidents

Revision ID: eee2_sentry_incident_hardening
Revises: ddd2_support_incident_verification
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "eee2_sentry_incident_hardening"
down_revision = "ddd2_support_incident_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sentry_incidents", sa.Column("subsystem_class", sa.String(32), nullable=True))
    op.add_column("sentry_incidents", sa.Column("merchant_impact", sa.String(16), nullable=True))
    op.add_column("sentry_incidents", sa.Column("affected_shop", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("sentry_incidents", "affected_shop")
    op.drop_column("sentry_incidents", "merchant_impact")
    op.drop_column("sentry_incidents", "subsystem_class")
