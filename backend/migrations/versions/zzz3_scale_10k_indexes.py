"""scale 10k indexes — product_metrics hot-scan composite + signal cursor helpers

Phase Ω⁷ post-audit scale hardening. The segment_monitor_worker queries
product_metrics with WHERE shop_domain = :s AND unique_visitors_24h >= N
ORDER BY unique_visitors_24h DESC and was doing an in-memory sort per shop.
At 10k merchants with 100+ products each this becomes a multi-second
operation per cycle.

Adding a composite index on (shop_domain, unique_visitors_24h DESC) lets
the planner satisfy both the WHERE and the ORDER BY from the index alone.

Uses CREATE INDEX CONCURRENTLY so the migration does not lock
product_metrics while it builds (critical at 10k scale). CONCURRENTLY
cannot run inside a transaction, hence the AUTOCOMMIT isolation.

Revision ID: zzz3_scale_10k_indexes
Revises: night_shift_reports, zzz2_email_journey_engine
Create Date: 2026-04-13
"""
from alembic import op

revision = "zzz3_scale_10k_indexes"
down_revision = ("night_shift_reports", "zzz2_email_journey_engine")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_product_metrics_shop_visitors
            ON product_metrics (shop_domain, unique_visitors_24h DESC)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_product_metrics_shop_visitors")
