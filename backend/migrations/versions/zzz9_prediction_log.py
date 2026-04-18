"""prediction_log table for MA-1 prediction-accuracy moat

Revision ID: zzz9_prediction_log
Revises: b86443ac80a1
Create Date: 2026-04-18

TIER_2 — explicit founder approval received 2026-04-18.

Why a new table (not audit_log)
-------------------------------
Predictions need to be REVISITED after their horizon passes so the
`actual_value` + `measured_at` can be filled in. audit_log is
append-only by convention and would have required a second "measured"
row per prediction — double writes, awkward joins, harder analytics.

A dedicated prediction_log:
  - columns (not JSON blobs) so accuracy queries index cleanly
  - UNIQUE (shop_domain, metric, horizon_date) atomic dedup
  - partial index on measured_at for fast "what's matured" lookups
  - schema matches the MAPE analytics shape 1:1

Schema
------
  id                 SERIAL PK
  created_at         TIMESTAMP NOT NULL        — when we made the prediction
  shop_domain        VARCHAR NOT NULL
  metric             VARCHAR(64) NOT NULL       — 'forecast_7d_revenue' etc
  prediction_date    DATE NOT NULL              — day we made it
  horizon_date       DATE NOT NULL              — day to measure actual
  predicted_value    NUMERIC(18, 2) NOT NULL
  predicted_low      NUMERIC(18, 2)             — CI lower (optional)
  predicted_high     NUMERIC(18, 2)             — CI upper (optional)
  currency           VARCHAR(8) NOT NULL DEFAULT 'USD'
  confidence         VARCHAR(16)                — 'high' | 'medium' | 'low'
  actual_value       NUMERIC(18, 2)             — nullable, filled post-horizon
  measured_at        TIMESTAMP                  — when actual was computed
  context_hash       VARCHAR(64)                — optional provenance tag

Indexes
-------
  UNIQUE (shop_domain, metric, horizon_date)    — atomic dedup
  ix_prediction_log_matured (shop_domain, horizon_date) WHERE actual_value IS NULL
      Covers the "which predictions need measuring?" query path.
  ix_prediction_log_shop_metric_created (shop_domain, metric, created_at DESC)
      Covers the "most recent predictions for shop × metric" query.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzz9_prediction_log"
down_revision: Union[str, Sequence[str], None] = "b86443ac80a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prediction_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("horizon_date", sa.Date(), nullable=False),
        sa.Column("predicted_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("predicted_low", sa.Numeric(18, 2), nullable=True),
        sa.Column("predicted_high", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "currency",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("actual_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("measured_at", sa.DateTime(), nullable=True),
        sa.Column("context_hash", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_prediction_log_shop_metric_horizon",
        "prediction_log",
        ["shop_domain", "metric", "horizon_date"],
    )
    op.create_index(
        "ix_prediction_log_matured",
        "prediction_log",
        ["shop_domain", "horizon_date"],
        postgresql_where=sa.text("actual_value IS NULL"),
    )
    op.create_index(
        "ix_prediction_log_shop_metric_created",
        "prediction_log",
        ["shop_domain", "metric", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prediction_log_shop_metric_created", table_name="prediction_log")
    op.drop_index("ix_prediction_log_matured", table_name="prediction_log")
    op.drop_constraint(
        "uq_prediction_log_shop_metric_horizon",
        "prediction_log",
        type_="unique",
    )
    op.drop_table("prediction_log")
