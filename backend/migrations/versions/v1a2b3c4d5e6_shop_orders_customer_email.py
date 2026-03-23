"""add customer_email to shop_orders

Revision ID: v1a2b3c4d5e6
Revises: s5e6f7a8b9c0
Create Date: 2026-03-23

Adds:
    shop_orders.customer_email   — nullable VARCHAR

Purpose:
    Enables cohort retention analysis (cohort_engine.py) and Klaviyo
    identity resolution (klaviyo_export.py) by storing the buyer's email
    from the Shopify orders/paid webhook payload.

    customer_email is extracted from:
        payload["customer"]["email"]  (primary)
        payload["email"]              (fallback — older Shopify webhook formats)

    NULL for guest checkouts or orders without an email address.
"""
from alembic import op
import sqlalchemy as sa

revision = "v1a2b3c4d5e6"
down_revision = "s5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shop_orders",
        sa.Column("customer_email", sa.String(), nullable=True),
    )
    # Index for cohort queries: WHERE shop_domain = X AND customer_email IS NOT NULL
    op.create_index(
        "ix_shop_orders_customer_email",
        "shop_orders",
        ["shop_domain", "customer_email"],
    )


def downgrade() -> None:
    op.drop_index("ix_shop_orders_customer_email", table_name="shop_orders")
    op.drop_column("shop_orders", "customer_email")
