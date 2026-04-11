"""commerce intelligence graph — cross-store cohorts + merchant mapping

Revision ID: sip5_commerce_intelligence_graph
Revises: sip4_adversarial_resilience
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip5_commerce_intelligence_graph"
down_revision = "sip4_adversarial_resilience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cig_cohorts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cohort_key", sa.String, nullable=False, unique=True, index=True),
        sa.Column("aov_band", sa.String(16), nullable=False),
        sa.Column("traffic_band", sa.String(16), nullable=False),
        sa.Column("mobile_band", sa.String(16), nullable=False),
        sa.Column("avg_cart_rate", sa.Float, nullable=True),
        sa.Column("avg_scroll_depth", sa.Float, nullable=True),
        sa.Column("avg_dwell_time", sa.Float, nullable=True),
        sa.Column("avg_return_rate", sa.Float, nullable=True),
        sa.Column("p25_cart_rate", sa.Float, nullable=True),
        sa.Column("p75_cart_rate", sa.Float, nullable=True),
        sa.Column("nudge_effectiveness", postgresql.JSONB, nullable=True),
        sa.Column("signal_distribution", postgresql.JSONB, nullable=True),
        sa.Column("price_sensitivity", postgresql.JSONB, nullable=True),
        sa.Column("traffic_quality", postgresql.JSONB, nullable=True),
        sa.Column("playbooks", postgresql.JSONB, nullable=True),
        sa.Column("merchant_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_data_points", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(8), nullable=False, server_default="low"),
        sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "cig_merchant_mappings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, index=True),
        sa.Column("primary_cohort_key", sa.String, nullable=True),
        sa.Column("primary_similarity", sa.Float, nullable=True),
        sa.Column("secondary_cohort_key", sa.String, nullable=True),
        sa.Column("secondary_similarity", sa.Float, nullable=True),
        sa.Column("tertiary_cohort_key", sa.String, nullable=True),
        sa.Column("tertiary_similarity", sa.Float, nullable=True),
        sa.Column("fingerprint", postgresql.JSONB, nullable=True),
        sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("shop_domain", name="uq_cig_mapping_shop"),
    )


def downgrade() -> None:
    op.drop_table("cig_merchant_mappings")
    op.drop_table("cig_cohorts")
