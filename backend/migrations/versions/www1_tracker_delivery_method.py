"""Add tracker_delivery_method to merchants.

Tracks how the storefront tracker is delivered to each merchant:
  - "script_tag" (current, default) — via Shopify Script Tags API
  - "theme_extension" (future) — via Shopify Theme App Extension
  - "manual" — manually injected by merchant (rare)

This column enables:
  - Fleet-wide visibility of delivery method distribution
  - Safe phased migration from Script Tags to TAE
  - Per-merchant migration status tracking

Revision ID: www1_tracker_delivery_method
Revises: vvv1_merchant_gdpr_consent
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "www1_tracker_delivery_method"
down_revision = "vvv1_merchant_gdpr_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("merchants", sa.Column(
        "tracker_delivery_method", sa.String(32), nullable=False,
        server_default="script_tag",
    ))


def downgrade() -> None:
    op.drop_column("merchants", "tracker_delivery_method")
