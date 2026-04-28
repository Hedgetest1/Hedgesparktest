"""merchant_saved_reports — Gap #1 Custom Report Builder (Lite)

Revision ID: zzzb_merchant_saved_reports
Revises: zzza_post_purchase_survey
Create Date: 2026-04-28

TIER_2 — sprint-scoped grant 2026-04-28 from founder
("Procedi" + "se 10/10 procedi avanti" + parity-doctrine clarification).

Why
---
Closes Gap #1 of `project_brutal_audit_0_70_2026_04_27.md` —
custom report builder. Per founder doctrine 2026-04-28, the FULL
builder ships in Lite (Better Reports $19.90 / Mipler $19 are in
the $0-60 band → parity → Lite per
`feedback_0_60_parity_doctrine.md`). Holdout-lift + peer-network
overlay also ship in Lite (HedgeSpark unique-value layer must be
visible at the entry tier where merchants meet HedgeSpark).
Anomaly detection callouts are explicitly OUT of this sprint
(Triple Whale $129+ → out of band → future Pro/Scale sprint).

Schema
------
A single table holds every saved-report config a merchant creates.
Report execution reads from pre-aggregated tables
(`product_metrics`, `cohort_summary`, `shop_orders` rollups) — never
raw events — to stay scale-safe at 10k merchants. Storage estimate:
10k × 5 reports avg = 50k config rows, ~25MB.

Soft-delete via `deleted_at` (per founder call: preserves audit
trail + 30d undo window).

Scheduling cap (1 daily / 1 weekly per shop) enforced via partial
UNIQUE constraint, not application logic.

Columns
-------
  id                   BIGSERIAL PK
  shop_domain          TEXT NOT NULL
  name                 VARCHAR(60) NOT NULL — merchant-given title
  metric               VARCHAR(40) NOT NULL — one of 12 catalog metrics, OR 'formula' when formula is set
  dimensions           JSONB NOT NULL DEFAULT '[]' — array of dimension keys (max 2)
  filters              JSONB NOT NULL DEFAULT '{}' — filter dict (max 3 keys)
  date_range_preset    VARCHAR(32) NOT NULL DEFAULT 'last_30_days' — preset key or 'custom'
  custom_start         DATE — only when preset='custom'
  custom_end           DATE — only when preset='custom'
  compare_enabled      BOOLEAN NOT NULL DEFAULT false
  formula              TEXT — optional custom formula expression (allow-listed tokens)
  forecast_horizon     INTEGER — 30/60/90 or NULL when forecast off
  scheduled            BOOLEAN NOT NULL DEFAULT false
  scheduled_cadence    VARCHAR(16) — 'daily' | 'weekly' | NULL
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  last_run_at          TIMESTAMPTZ
  deleted_at           TIMESTAMPTZ — soft-delete tombstone

Indexes
-------
  idx_msr_shop_updated         (shop_domain, updated_at DESC) WHERE deleted_at IS NULL
  idx_msr_scheduled            (shop_domain) WHERE scheduled = true AND deleted_at IS NULL
  uq_msr_shop_name             UNIQUE (shop_domain, name) WHERE deleted_at IS NULL
  uq_msr_shop_cadence          UNIQUE (shop_domain, scheduled_cadence)
                               WHERE scheduled = true AND deleted_at IS NULL
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzzb_merchant_saved_reports"
down_revision: Union[str, Sequence[str], None] = "zzza_post_purchase_survey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merchant_saved_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column(
            "dimensions",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "filters",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "date_range_preset",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'last_30_days'"),
        ),
        sa.Column("custom_start", sa.Date(), nullable=True),
        sa.Column("custom_end", sa.Date(), nullable=True),
        sa.Column(
            "compare_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("forecast_horizon", sa.Integer(), nullable=True),
        sa.Column(
            "scheduled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("scheduled_cadence", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Active reports for the dashboard list (Reports Hub)
    op.create_index(
        "idx_msr_shop_updated",
        "merchant_saved_reports",
        ["shop_domain", sa.text("updated_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Email orchestrator finds scheduled reports cheaply
    op.create_index(
        "idx_msr_scheduled",
        "merchant_saved_reports",
        ["shop_domain"],
        postgresql_where=sa.text("scheduled = true AND deleted_at IS NULL"),
    )
    # No two ACTIVE reports with the same name per shop
    op.create_index(
        "uq_msr_shop_name",
        "merchant_saved_reports",
        ["shop_domain", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Schedule cap enforcement: 1 daily + 1 weekly slot per shop
    op.create_index(
        "uq_msr_shop_cadence",
        "merchant_saved_reports",
        ["shop_domain", "scheduled_cadence"],
        unique=True,
        postgresql_where=sa.text("scheduled = true AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_msr_shop_cadence", table_name="merchant_saved_reports")
    op.drop_index("uq_msr_shop_name", table_name="merchant_saved_reports")
    op.drop_index("idx_msr_scheduled", table_name="merchant_saved_reports")
    op.drop_index("idx_msr_shop_updated", table_name="merchant_saved_reports")
    op.drop_table("merchant_saved_reports")
