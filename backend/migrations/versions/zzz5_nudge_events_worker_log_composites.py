"""scale 10k — nudge_events + worker_log composite indexes

Continuation of the Stage-1 post-hardening scale audit (2026-04-14).
After closing the autonomous_actions index gaps in zzz4, the same
maniacal sweep flagged two more tables with missing composites:

NUDGE_EVENTS:
  Existing indexes cover (shop_domain, nudge_id, event_type) perfectly
  for the nudge_measurement hot path, plus (shop_domain, visitor_id)
  for identity lookups and a lone (created_at) for global retention.
  Missing: (shop_domain, created_at) for the time-window queries in
  revenue_at_risk, nudge_dna, data_integrity_probe, and
  evolution_causal_attribution — every query that rolls up "last 30
  days of nudge activity for this shop" currently has to use the
  shop_domain index and filter created_at in memory, which is
  O(total_shop_nudges) instead of O(recent_shop_nudges). At 10k
  merchants × 100 events/shop/day the difference is 100× at full
  table maturity.

WORKER_LOG:
  Existing indexes: (started_at) for global retention DELETE and
  (worker_name) for name-only lookups. Missing the composite used by
  the /ops dashboard, the watchdog task, and system_health_synthesizer
  — they all do WHERE worker_name = ? ORDER BY started_at DESC LIMIT 1
  to get "latest run of worker X". Without the composite, Postgres
  scans the name-only index and sorts in memory; with it, the walk
  terminates at the first row.

Write-cost consideration:
  - nudge_events receives many inserts per day (every impression);
    each new index adds ~10μs per insert. At 10k merchants doing
    100 events/day = 12 inserts/sec average, that's ~120μs/sec CPU
    overhead. Negligible against the read wins.
  - worker_log receives one insert per worker cycle (tens per minute
    across all workers). The write cost is invisible.

Both indexes created CONCURRENTLY so production merchants are not
locked out while the index builds.

Revision ID: zzz5_nudge_events_worker_log_composites
Revises: zzz4_autonomous_actions_composite_indexes
Create Date: 2026-04-14
"""
from alembic import op

revision = "zzz5_nudge_events_worker_log_composites"
down_revision = "zzz4_autonomous_actions_composite_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # nudge_events time-window rollups (revenue_at_risk, nudge_dna,
        # data_integrity_probe, evolution_causal_attribution).
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_nudge_events_shop_created
            ON nudge_events (shop_domain, created_at)
            """
        )

        # worker_log "latest run of worker X" — /ops dashboard,
        # watchdog, system_health_synthesizer.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_worker_log_name_started
            ON worker_log (worker_name, started_at DESC)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_worker_log_name_started")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_nudge_events_shop_created")
