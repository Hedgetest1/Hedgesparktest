"""Add pixel_secret column to merchants.

Per-merchant secret embedded in the Shopify Custom Pixel code.
Validated server-side on purchase events to prevent spoofed revenue injection.

Revision ID: ii1_merchant_pixel_secret
Revises: hh1_merchant_contact_email
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = "ii1_merchant_pixel_secret"
down_revision = "hh1_merchant_contact_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("pixel_secret", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "pixel_secret")
