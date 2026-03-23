"""phase1: add product_metrics, worker_state, worker_log tables

Revision ID: e2f5b8a3c1d7
Revises: d1f4c9e2a7b3
Create Date: 2026-03-18

Creates the three infrastructure tables required by Phase 1:

  product_metrics  — pre-aggregated per-(shop, product) behavioral
                     counters written by the aggregation worker and
                     read by the signal detection engine.

  worker_state     — one row per worker process; stores last_run_at
                     and last_watermark for incremental processing.

  worker_log       — append-only cycle execution log for all workers;
                     enables structured observability without log files.

These tables contain no business logic.  They are pure infrastructure.
This migration runs inside a normal transaction and is fully reversible.

The events(shop_domain, timestamp DESC) index lives in the NEXT
migration (f3a6c9b4d2e8) because CREATE INDEX CONCURRENTLY cannot
run inside a transaction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f5b8a3c1d7"
down_revision: Union[str, None] = "d1f4c9e2a7b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # product_metrics                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "product_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("product_url", sa.String(), nullable=False),
        sa.Column("views_1h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("views_24h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("views_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_visitors_24h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_visitors_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cart_conversions_24h", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("return_visitor_count_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_dwell_24h", sa.Float(), nullable=True),
        sa.Column("avg_scroll_24h", sa.Float(), nullable=True),
        sa.Column("last_event_at", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_domain",
            "product_url",
            name="uq_product_metrics_shop_product",
        ),
    )
    # Index on shop_domain for fast per-shop reads in the detection engine.
    op.create_index(
        "ix_product_metrics_shop_domain",
        "product_metrics",
        ["shop_domain"],
    )

    # ------------------------------------------------------------------ #
    # worker_state                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "worker_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_name", sa.String(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_watermark", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("worker_name", name="uq_worker_state_name"),
    )

    # ------------------------------------------------------------------ #
    # worker_log                                                           #
    # ------------------------------------------------------------------ #
    op.create_table(
        "worker_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("worker_name", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("shops_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_detail", sa.String(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_worker_log_worker_name",
        "worker_log",
        ["worker_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_log_worker_name", table_name="worker_log")
    op.drop_table("worker_log")

    op.drop_table("worker_state")

    op.drop_index("ix_product_metrics_shop_domain", table_name="product_metrics")
    op.drop_table("product_metrics")
