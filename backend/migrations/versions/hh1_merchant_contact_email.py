"""Add contact_email column to merchants.

Persists the shop owner email from Shopify's shop.json API at install time.
Used as the recipient for weekly revenue digest emails.

Revision ID: hh1_merchant_contact_email
Revises: gg1_order_source_column
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = "hh1_merchant_contact_email"
down_revision = "gg1_order_source_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("contact_email", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "contact_email")
