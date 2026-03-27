"""Add purchase attribution, time-of-day, and session context columns.

product_metrics gains 15 columns:

  Purchase attribution (7d window):
    purchases_24h, purchases_7d, revenue_24h,
    purchases_mobile, purchases_desktop,
    purchases_paid, purchases_organic, purchases_direct

  Time-of-day intelligence (24h window):
    peak_hour_views, peak_hour_carts,
    off_peak_hour_views, off_peak_hour_carts

  Session context (24h window):
    landing_views_24h, browsing_views_24h,
    landing_carts_24h, browsing_carts_24h
"""

from alembic import op
import sqlalchemy as sa

revision = "mm1_purchase_time_session_columns"
down_revision = "ll1_data_segmentation_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Purchase attribution --
    op.add_column("product_metrics", sa.Column("purchases_24h", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_7d", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("revenue_24h", sa.Float, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_mobile", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_desktop", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_paid", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_organic", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("purchases_direct", sa.Integer, nullable=False, server_default="0"))

    # -- Time-of-day intelligence --
    op.add_column("product_metrics", sa.Column("peak_hour_views", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("peak_hour_carts", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("off_peak_hour_views", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("off_peak_hour_carts", sa.Integer, nullable=False, server_default="0"))

    # -- Session context --
    op.add_column("product_metrics", sa.Column("landing_views_24h", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("browsing_views_24h", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("landing_carts_24h", sa.Integer, nullable=False, server_default="0"))
    op.add_column("product_metrics", sa.Column("browsing_carts_24h", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("product_metrics", "browsing_carts_24h")
    op.drop_column("product_metrics", "landing_carts_24h")
    op.drop_column("product_metrics", "browsing_views_24h")
    op.drop_column("product_metrics", "landing_views_24h")
    op.drop_column("product_metrics", "off_peak_hour_carts")
    op.drop_column("product_metrics", "off_peak_hour_views")
    op.drop_column("product_metrics", "peak_hour_carts")
    op.drop_column("product_metrics", "peak_hour_views")
    op.drop_column("product_metrics", "purchases_direct")
    op.drop_column("product_metrics", "purchases_organic")
    op.drop_column("product_metrics", "purchases_paid")
    op.drop_column("product_metrics", "purchases_desktop")
    op.drop_column("product_metrics", "purchases_mobile")
    op.drop_column("product_metrics", "revenue_24h")
    op.drop_column("product_metrics", "purchases_7d")
    op.drop_column("product_metrics", "purchases_24h")
