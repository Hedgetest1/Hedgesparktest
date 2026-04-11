"""store intelligence profiles — per-merchant learned intelligence + weekly snapshots

Revision ID: sip1_store_intelligence_profiles
Revises: zzz2_email_journey_engine
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip1_store_intelligence_profiles"
down_revision = ("qqq1_synthetic_merchant_flag", "zzz2_email_journey_engine")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "store_intelligence_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, unique=True, index=True),
        sa.Column("profile_version", sa.Integer, nullable=False, server_default="1"),

        # Behavioral baselines
        sa.Column("baseline_cart_rate", sa.Float, nullable=True),
        sa.Column("baseline_scroll_depth", sa.Float, nullable=True),
        sa.Column("baseline_dwell_time", sa.Float, nullable=True),
        sa.Column("baseline_return_rate", sa.Float, nullable=True),
        sa.Column("baseline_views_per_product", sa.Float, nullable=True),
        sa.Column("baseline_mobile_pct", sa.Float, nullable=True),

        # Learned thresholds (JSONB)
        sa.Column("learned_thresholds", postgresql.JSONB, nullable=True),

        # Traffic source quality (JSONB)
        sa.Column("traffic_source_quality", postgresql.JSONB, nullable=True),

        # Price sensitivity bands (JSONB)
        sa.Column("price_sensitivity_bands", postgresql.JSONB, nullable=True),

        # Nudge effectiveness (JSONB)
        sa.Column("nudge_type_scores", postgresql.JSONB, nullable=True),
        sa.Column("best_nudge_by_signal", postgresql.JSONB, nullable=True),

        # Temporal patterns (JSONB)
        sa.Column("peak_traffic_hours", postgresql.JSONB, nullable=True),

        # Signal history (JSONB)
        sa.Column("signal_frequency_30d", postgresql.JSONB, nullable=True),

        # Confidence
        sa.Column("data_points_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence_level", sa.String(8), nullable=False, server_default="low"),

        # Timestamps
        sa.Column("computed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "sip_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, index=True),
        sa.Column("snapshot_week", sa.DateTime, nullable=False),
        sa.Column("profile_data", postgresql.JSONB, nullable=False),
        sa.Column("baseline_cart_rate", sa.Float, nullable=True),
        sa.Column("data_points", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("shop_domain", "snapshot_week", name="uq_sip_snapshot_shop_week"),
    )


def downgrade() -> None:
    op.drop_table("sip_snapshots")
    op.drop_table("store_intelligence_profiles")
