"""Create relational execution tracking tables; drop JSONB column.

Replaces store_metrics.execution_opportunities (JSONB) with three
proper relational tables:

  execution_opportunities — one row per (shop, type, product_a, product_b)
  execution_audiences     — visitor membership per opportunity
  execution_tracking      — outcome measurement per visitor per opportunity

This enables:
  - scalable audience storage (no JSONB arrays)
  - incremental outcome tracking (returned, viewed, purchased)
  - real proof loop (rates computed from relational counts)
"""

from alembic import op
import sqlalchemy as sa

revision = "oo1_execution_tracking_tables"
down_revision = "nn1_store_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- execution_opportunities: one persistent row per opportunity --
    op.create_table(
        "execution_opportunities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.String(12), nullable=False),
        sa.Column("shop_domain", sa.String, nullable=False),
        sa.Column("opp_type", sa.String(16), nullable=False),  # upsell | bundle
        sa.Column("product_a", sa.String, nullable=False),
        sa.Column("product_b", sa.String, nullable=False),
        sa.Column("audience_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("suggested_message", sa.Text, nullable=True),
        sa.Column("timing", sa.String(128), nullable=True),
        sa.Column("expected_impact", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("refreshed_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_exec_opp_shop", "execution_opportunities", ["shop_domain"])
    op.create_index(
        "uq_exec_opp_id",
        "execution_opportunities",
        ["shop_domain", "execution_id"],
        unique=True,
    )

    # -- execution_audiences: visitor membership per opportunity --
    op.create_table(
        "execution_audiences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.String(12), nullable=False),
        sa.Column("shop_domain", sa.String, nullable=False),
        sa.Column("visitor_id", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_exec_aud_shop_exec", "execution_audiences", ["shop_domain", "execution_id"])
    op.create_index("ix_exec_aud_visitor", "execution_audiences", ["visitor_id"])
    op.create_index(
        "uq_exec_aud_exec_visitor",
        "execution_audiences",
        ["execution_id", "visitor_id"],
        unique=True,
    )

    # -- execution_tracking: outcome measurement per visitor --
    op.create_table(
        "execution_tracking",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.String(12), nullable=False),
        sa.Column("shop_domain", sa.String, nullable=False),
        sa.Column("visitor_id", sa.String, nullable=False),
        sa.Column("exposed_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("returned", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("viewed_product_b", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("purchased_product_b", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_exec_track_shop_exec", "execution_tracking", ["shop_domain", "execution_id"])
    op.create_index("ix_exec_track_visitor", "execution_tracking", ["visitor_id"])
    op.create_index(
        "uq_exec_track_exec_visitor",
        "execution_tracking",
        ["execution_id", "visitor_id"],
        unique=True,
    )

    # -- Drop JSONB column from store_metrics --
    op.drop_column("store_metrics", "execution_opportunities")


def downgrade() -> None:
    op.add_column(
        "store_metrics",
        sa.Column("execution_opportunities", sa.JSON, nullable=False, server_default="[]"),
    )
    op.drop_table("execution_tracking")
    op.drop_table("execution_audiences")
    op.drop_table("execution_opportunities")
