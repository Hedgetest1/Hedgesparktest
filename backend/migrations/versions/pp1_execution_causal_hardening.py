"""Execution confirmation, baselines, and post-execution measurement.

Adds to execution_opportunities:
  - execution_status (suggested|acknowledged|executed|paused|completed)
  - executed_at, execution_mode, execution_note
  - post-execution measurement columns (precomputed by worker)

Creates execution_baselines table:
  - one row per executed opportunity, captured AT execution time
  - stores pre-execution proof rates as the "before" reference

Enables the causal confidence layer:
  baseline (captured at execution) vs post-execution outcomes → delta + confidence
"""

from alembic import op
import sqlalchemy as sa

revision = "pp1_execution_causal_hardening"
down_revision = "oo1_execution_tracking_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Execution confirmation fields on execution_opportunities --
    op.add_column("execution_opportunities",
        sa.Column("execution_status", sa.String(16), nullable=False, server_default="suggested"))
    op.add_column("execution_opportunities",
        sa.Column("executed_at", sa.DateTime, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("execution_mode", sa.String(16), nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("execution_note", sa.String(500), nullable=True))

    # -- Post-execution measurement (precomputed by worker) --
    op.add_column("execution_opportunities",
        sa.Column("post_return_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("post_view_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("post_purchase_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("post_sample_size", sa.Integer, nullable=False, server_default="0"))
    op.add_column("execution_opportunities",
        sa.Column("confidence_label", sa.String(16), nullable=True))
    # delta = post - baseline (positive = improvement)
    op.add_column("execution_opportunities",
        sa.Column("delta_return_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("delta_view_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("delta_purchase_rate", sa.Float, nullable=True))

    op.create_index("ix_exec_opp_status", "execution_opportunities",
                    ["shop_domain", "execution_status"])

    # -- Execution baselines table --
    op.create_table(
        "execution_baselines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("execution_id", sa.String(12), nullable=False),
        sa.Column("shop_domain", sa.String, nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        # Pre-execution proof rates (from execution_tracking at time of execution)
        sa.Column("audience_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("return_rate", sa.Float, nullable=True),
        sa.Column("view_rate", sa.Float, nullable=True),
        sa.Column("purchase_rate", sa.Float, nullable=True),
        sa.Column("tracked_count", sa.Integer, nullable=False, server_default="0"),
        # Product B context at baseline time
        sa.Column("product_b", sa.String, nullable=True),
        sa.Column("product_b_views_24h", sa.Integer, nullable=True),
        sa.Column("product_b_carts_24h", sa.Integer, nullable=True),
        sa.Column("product_b_purchases_24h", sa.Integer, nullable=True),
        sa.Column("product_b_revenue_24h", sa.Float, nullable=True),
    )
    op.create_index("uq_exec_baseline_id",
        "execution_baselines", ["shop_domain", "execution_id"], unique=True)


def downgrade() -> None:
    op.drop_table("execution_baselines")
    op.drop_index("ix_exec_opp_status", table_name="execution_opportunities")
    for col in [
        "delta_purchase_rate", "delta_view_rate", "delta_return_rate",
        "confidence_label", "post_sample_size",
        "post_purchase_rate", "post_view_rate", "post_return_rate",
        "execution_note", "execution_mode", "executed_at", "execution_status",
    ]:
        op.drop_column("execution_opportunities", col)
