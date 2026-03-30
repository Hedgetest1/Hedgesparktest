"""Add gdpr_consent_at to merchants.

Tracks when the merchant implicitly consented to data processing by
installing the Shopify app.  Set at OAuth completion time.

Revision ID: vvv1_merchant_gdpr_consent
Revises: uuu1_attribution_columns
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "vvv1_merchant_gdpr_consent"
down_revision = "uuu1_attribution_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("merchants", sa.Column("gdpr_consent_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("merchants", "gdpr_consent_at")
