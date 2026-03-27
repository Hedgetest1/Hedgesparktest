"""Create store_metrics table for precomputed store-level intelligence.

One row per shop_domain. Written by aggregation_worker every cycle.
Contains co-viewed product pairs, cohort snapshot, and execution opportunities.

Eliminates runtime event-table queries from the store intelligence API.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "nn1_store_metrics"
down_revision = "mm1_purchase_time_session_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, unique=True),

        # Co-viewed product pairs (top 10, JSONB array)
        # Each: {product_a, product_b, shared_visitors, a_views, b_views}
        sa.Column("co_viewed_pairs", JSONB, nullable=False, server_default="[]"),

        # Cohort snapshot
        sa.Column("new_visitors_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("returning_visitors_7d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("new_visitor_cart_rate", sa.Float, nullable=True),
        sa.Column("returning_visitor_cart_rate", sa.Float, nullable=True),

        # Execution opportunities (JSONB array for proof loop)
        # Each: {id, type, product_a, product_b, audience_size, audience_visitor_ids,
        #         suggested_message, created_at}
        sa.Column("execution_opportunities", JSONB, nullable=False, server_default="[]"),

        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_store_metrics_shop", "store_metrics", ["shop_domain"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_store_metrics_shop", table_name="store_metrics")
    op.drop_table("store_metrics")
