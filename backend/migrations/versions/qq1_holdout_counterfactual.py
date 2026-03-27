"""Add holdout/control group support for counterfactual measurement.

execution_audiences + execution_tracking gain group_type (exposed|holdout).
execution_opportunities gains holdout_pct, per-group rates, and lift columns.

Enables exposed vs holdout comparison for causal evidence.
"""

from alembic import op
import sqlalchemy as sa

revision = "qq1_holdout_counterfactual"
down_revision = "pp1_execution_causal_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- group_type on execution_audiences --
    op.add_column("execution_audiences",
        sa.Column("group_type", sa.String(8), nullable=False, server_default="exposed"))
    op.create_index("ix_exec_aud_group",
        "execution_audiences", ["execution_id", "group_type"])

    # -- group_type on execution_tracking --
    op.add_column("execution_tracking",
        sa.Column("group_type", sa.String(8), nullable=False, server_default="exposed"))
    op.create_index("ix_exec_track_group",
        "execution_tracking", ["execution_id", "group_type"])

    # -- holdout config on execution_opportunities --
    op.add_column("execution_opportunities",
        sa.Column("holdout_pct", sa.Integer, nullable=False, server_default="20"))

    # -- per-group sample sizes --
    op.add_column("execution_opportunities",
        sa.Column("exposed_sample_size", sa.Integer, nullable=False, server_default="0"))
    op.add_column("execution_opportunities",
        sa.Column("holdout_sample_size", sa.Integer, nullable=False, server_default="0"))

    # -- per-group rates (exposed) --
    op.add_column("execution_opportunities",
        sa.Column("return_rate_exposed", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("view_rate_exposed", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("purchase_rate_exposed", sa.Float, nullable=True))

    # -- per-group rates (holdout) --
    op.add_column("execution_opportunities",
        sa.Column("return_rate_holdout", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("view_rate_holdout", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("purchase_rate_holdout", sa.Float, nullable=True))

    # -- lift (exposed - holdout) --
    op.add_column("execution_opportunities",
        sa.Column("lift_return_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("lift_view_rate", sa.Float, nullable=True))
    op.add_column("execution_opportunities",
        sa.Column("lift_purchase_rate", sa.Float, nullable=True))


def downgrade() -> None:
    for col in [
        "lift_purchase_rate", "lift_view_rate", "lift_return_rate",
        "purchase_rate_holdout", "view_rate_holdout", "return_rate_holdout",
        "purchase_rate_exposed", "view_rate_exposed", "return_rate_exposed",
        "holdout_sample_size", "exposed_sample_size", "holdout_pct",
    ]:
        op.drop_column("execution_opportunities", col)
    op.drop_index("ix_exec_track_group", table_name="execution_tracking")
    op.drop_column("execution_tracking", "group_type")
    op.drop_index("ix_exec_aud_group", table_name="execution_audiences")
    op.drop_column("execution_audiences", "group_type")
