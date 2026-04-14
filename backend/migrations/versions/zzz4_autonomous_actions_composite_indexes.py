"""scale 10k — autonomous_actions composite indexes

Post-Stage-1 hardening audit (§12 scale checklist, 2026-04-14). The
autonomous_actions table was created with a single-column
ix_autonomous_actions_shop_domain and ix_autonomous_actions_status
index pair. At 10k merchants with a mature autonomous loop firing
multiple decisions per merchant per day, the table can reach tens of
millions of rows within a quarter. Every query that filters on
shop_domain plus a second predicate was previously:
  Index Scan on ix_autonomous_actions_shop_domain
    → load ALL rows for the shop
    → in-memory Filter on the second predicate
    → in-memory Sort
    → Limit

Verified via EXPLAIN ANALYZE on the new GET /pro/night-shift/timeline
endpoint — which runs WHERE shop_domain = ? AND created_at >= ? ORDER
BY created_at DESC LIMIT 200 — and reproduced on three other query
call sites:

  1. app/api/night_shift.py::get_timeline
     WHERE shop_domain = ? AND created_at >= ? ORDER BY created_at DESC

  2. app/api/roi_hero.py (4 call sites)
     WHERE shop_domain = ? AND outcome IN ('win', 'measured')
       AND measurement_end >= ?

  3. app/services/autonomous_loop.py::load_active_actions
     WHERE shop_domain = ? AND status IN ('deployed', 'measuring')

Three new composite indexes close the debt. Write cost is negligible
because autonomous_actions receives at most one INSERT per autonomous
decision (~1/merchant/15min); the read wins are many orders of
magnitude at scale.

All three are created CONCURRENTLY so production merchants are not
locked out while the index builds.

Revision ID: zzz4_autonomous_actions_composite_indexes
Revises: zzz3_scale_10k_indexes
Create Date: 2026-04-14
"""
from alembic import op

revision = "zzz4_autonomous_actions_composite_indexes"
down_revision = "zzz3_scale_10k_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction
    with op.get_context().autocommit_block():
        # (1) Timeline + recent-actions lookups: WHERE shop_domain = ?
        #     AND created_at >= ? ORDER BY created_at DESC LIMIT N.
        #     Lets the planner walk the index in DESC order and stop
        #     at LIMIT N instead of loading every row for the shop.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_autonomous_actions_shop_created
            ON autonomous_actions (shop_domain, created_at DESC)
            """
        )

        # (2) roi_hero holdout-measured rollups: WHERE shop_domain = ?
        #     AND outcome IN ('win', 'measured') AND measurement_end >= ?.
        #     Three-column composite so outcome can be an index condition
        #     (IN-list) and measurement_end can be a range scan. This
        #     removes the hot 30-day window from a full shop scan.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_autonomous_actions_shop_outcome_measured
            ON autonomous_actions (shop_domain, outcome, measurement_end)
            """
        )

        # (3) Autonomous loop hot path: WHERE shop_domain = ? AND status
        #     IN ('deployed', 'measuring'). Called every loop cycle for
        #     every shop to pull currently-running experiments. The
        #     separate shop_domain and status indexes required a bitmap
        #     scan; the composite serves the query from a single walk.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_autonomous_actions_shop_status
            ON autonomous_actions (shop_domain, status)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_autonomous_actions_shop_status")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_autonomous_actions_shop_outcome_measured")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_autonomous_actions_shop_created")
