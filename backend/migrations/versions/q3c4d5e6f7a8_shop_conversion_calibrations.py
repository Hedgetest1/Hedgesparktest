"""Create shop_conversion_calibrations table.

Stores one empirical conversion calibration record per shop.
Updated on demand (lazy retrain) when the record is stale (>6 hours old)
or when explicitly retrained.

Each record holds:
  - the training dataset statistics (sample counts, behavioral means)
  - the derived calibration parameters (base_cvr, discriminability)
  - metadata for observability and fallback decisions

No training data is stored — only the derived statistics.  If the store
is dropped and recreated, the model is retrained from events and
visitor_purchase_sessions on the next request.

Revision ID: q3c4d5e6f7a8
Revises: p2b3c4d5e6f7
"""
from alembic import op
import sqlalchemy as sa

revision = "q3c4d5e6f7a8"
down_revision = "p2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_conversion_calibrations",

        sa.Column("id", sa.Integer(), nullable=False),

        # One row per shop — UNIQUE enforced below
        sa.Column("shop_domain", sa.String(), nullable=False),

        # Monotonic version counter — increments on each retrain
        sa.Column("model_version", sa.Integer(), nullable=False, server_default="1"),

        # Training window used to build this calibration
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="30"),

        # Training dataset size
        sa.Column("sample_size",     sa.Integer(), nullable=False),  # total product-viewing visitors
        sa.Column("converter_count", sa.Integer(), nullable=False),  # attributed purchasers

        # Core calibration parameters
        sa.Column("base_cvr",                    sa.Float(), nullable=False),  # shop-wide empirical CVR
        sa.Column("converter_behavioral_mean",   sa.Float(), nullable=False),  # avg behavioral_index of converters
        sa.Column("non_converter_behavioral_mean", sa.Float(), nullable=False),  # avg behavioral_index of non-converters
        sa.Column("discriminability",            sa.Float(), nullable=False),  # converter_mean - non_converter_mean

        # Whether data was sufficient for empirical use.
        # False → calibration exists but model is in fallback mode (returns inferred).
        sa.Column("is_empirical", sa.Boolean(), nullable=False, server_default="false"),

        # When this calibration was last computed
        sa.Column("trained_at", sa.DateTime(), nullable=False),

        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_domain", name="uq_scc_shop_domain"),
    )

    op.create_index(
        "ix_scc_shop_domain",
        "shop_conversion_calibrations",
        ["shop_domain"],
    )


def downgrade() -> None:
    op.drop_index("ix_scc_shop_domain", table_name="shop_conversion_calibrations")
    op.drop_table("shop_conversion_calibrations")
