"""Add device, temporal, and source segmentation columns.

product_metrics gains 11 columns:
  - Device:   views_mobile, views_desktop, carts_mobile, carts_desktop
  - Temporal:  cart_conversions_7d
  - Source:    views_paid, views_organic, views_direct,
               carts_paid, carts_organic, carts_direct

events gains 1 column:
  - utm_medium: raw utm_medium param for paid/organic classification
"""

from alembic import op
import sqlalchemy as sa

revision = "ll1_data_segmentation_columns"
down_revision = "kk1_events_device_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Device segmentation on product_metrics --
    op.add_column("product_metrics", sa.Column("views_mobile", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("views_desktop", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("carts_mobile", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("carts_desktop", sa.Integer, nullable=False, server_default="0"))

    # -- Temporal: 7-day cart conversions for trend comparison --
    op.add_column("product_metrics", sa.Column("cart_conversions_7d", sa.Integer, nullable=False, server_default="0"))

    # -- Source segmentation (paid / organic / direct) --
    op.add_column("product_metrics", sa.Column("views_paid", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("views_organic", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("views_direct", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("carts_paid", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("carts_organic", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("carts_direct", sa.Integer, nullable=False, server_default="0"))

    # -- UTM medium on events for paid/organic classification --
    op.add_column("events", sa.Column("utm_medium", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "utm_medium")

    op.drop_column("product_metrics", "carts_direct")
    op.drop_column("product_metrics", "carts_organic")
    op.drop_column("product_metrics", "carts_paid")
    op.drop_column("product_metrics", "views_direct")
    op.drop_column("product_metrics", "views_organic")
    op.drop_column("product_metrics", "views_paid")
    op.drop_column("product_metrics", "cart_conversions_7d")
    op.drop_column("product_metrics", "carts_desktop")
    op.drop_column("product_metrics", "carts_mobile")
    op.drop_column("product_metrics", "views_desktop")
    op.drop_column("product_metrics", "views_mobile")
