"""shop_orders_class_d_columns

Adds 6 nullable columns to shop_orders for Class D base-analytics
(2026-04-26 founder directive — base-analytics parity vs $0-70
competitors). All NULLABLE so old orders without pixel-side
enrichment stay valid; new orders carry the data going forward
once spark-pixel.js v14 lands in the merchant's Custom Pixel.

Columns:
  discount_amount       NUMERIC(12,2)   total discount applied (in order currency)
  discount_codes        JSONB           array of codes used: ["SUMMER10", "FREESHIP"]
  tax_amount            NUMERIC(12,2)   total tax (in order currency)
  payment_method        VARCHAR(64)     "shopify_payments" / "paypal" / "stripe" / etc.
  financial_status      VARCHAR(32)     "paid" / "pending" / "authorized" / "refunded"
  fulfillment_status    VARCHAR(32)     "fulfilled" / "unfulfilled" / "partial"

No NOT NULL backfill — these are forward-looking analytics. Empty
endpoint state surfaces "no data yet" until the new pixel ships
and orders accumulate.

Revision ID: ecb1659d2093
Revises: bbb3_bugfix_candidate_iteration_num
Create Date: 2026-04-26 20:34:52.214013
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'ecb1659d2093'
down_revision: Union[str, Sequence[str], None] = 'bbb3_bugfix_candidate_iteration_num'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "shop_orders",
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "shop_orders",
        sa.Column("discount_codes", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "shop_orders",
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "shop_orders",
        sa.Column("payment_method", sa.String(64), nullable=True),
    )
    op.add_column(
        "shop_orders",
        sa.Column("financial_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "shop_orders",
        sa.Column("fulfillment_status", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("shop_orders", "fulfillment_status")
    op.drop_column("shop_orders", "financial_status")
    op.drop_column("shop_orders", "payment_method")
    op.drop_column("shop_orders", "tax_amount")
    op.drop_column("shop_orders", "discount_codes")
    op.drop_column("shop_orders", "discount_amount")
