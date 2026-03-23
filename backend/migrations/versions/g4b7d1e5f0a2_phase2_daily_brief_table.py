"""phase2: add daily_brief table

Revision ID: g4b7d1e5f0a2
Revises: f3a6c9b4d2e8
Create Date: 2026-03-18

Adds the daily_brief table required by Phase 2.

  daily_brief  — one row per (shop_domain, brief_date).  Written by
                 brief_engine.generate_brief(), served cold by the
                 /brief/today endpoint, and refreshed once per calendar
                 day by the aggregation worker (Phase 2 integration step).

The table is pure merchant-facing output: no raw events are stored here.
All values are derived deterministically from product_metrics by
brief_engine.py.  summary_text is the only optional AI-populated column
(Pro plan, Phase 2 optional step); all other columns are always present.

metrics_snapshot stores the top 3 products as a JSON text string so the
/brief/today endpoint can return complete card data in a single read,
without a second query.  Text (not JSONB) is used for broad portability
and consistency with the rest of the model layer.

This migration runs inside a normal transaction and is fully reversible.
No CONCURRENTLY index required.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g4b7d1e5f0a2"
down_revision: Union[str, None] = "f3a6c9b4d2e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_brief",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=False),
        # Calendar date this brief covers, e.g. 2026-03-18.
        # Combined with shop_domain as the idempotency key.
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # The single merchant-facing headline sentence.
        # Always present; uses "No product signals yet" copy on empty state.
        sa.Column("headline", sa.String(), nullable=False),
        # Top opportunity product.  All four nullable — populated whenever
        # at least one product_metrics row exists for the shop.
        sa.Column("top_product_url", sa.String(), nullable=True),
        sa.Column("top_product_label", sa.String(), nullable=True),
        sa.Column("top_signal_type", sa.String(), nullable=True),
        sa.Column("top_action", sa.String(), nullable=True),
        # Count of distinct signal instances detected this cycle.
        sa.Column(
            "signals_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # Top 3 products serialised as a JSON string.
        # Schema: list of {product_url, product_label, signal_type,
        #                   signal_strength, human_label, human_action}
        sa.Column("metrics_snapshot", sa.Text(), nullable=True),
        # Pro plan: AI-generated narrative paragraph (optional).
        # NULL until the optional AI worker populates it.
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column(
            "summary_generated",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_domain",
            "brief_date",
            name="uq_daily_brief_shop_date",
        ),
    )
    # Index on shop_domain for fast per-shop reads in the /brief/today endpoint.
    op.create_index(
        "ix_daily_brief_shop_domain",
        "daily_brief",
        ["shop_domain"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_brief_shop_domain", table_name="daily_brief")
    op.drop_table("daily_brief")
