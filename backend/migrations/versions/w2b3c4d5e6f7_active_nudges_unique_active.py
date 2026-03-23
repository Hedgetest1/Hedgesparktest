"""active_nudges: partial unique index for one-active-nudge-per-triple rule

Revision ID: w2b3c4d5e6f7
Revises: v1a2b3c4d5e6
Create Date: 2026-03-23

Adds:
    UNIQUE INDEX on active_nudges (shop_domain, product_url, action_type)
    WHERE status = 'active'

Purpose
-------
The nudge_engine enforces the one-active-nudge-per-(shop, product, action_type)
rule at the service layer.  Under concurrent requests (multiple dashboard tabs,
hot_segment_monitor + manual creation race), this service-layer check can be
bypassed because two writers can both pass the SELECT before either INSERT
commits.

This partial unique index adds DB-level enforcement as a safety net:
  - Only ONE active nudge row may exist for each (shop, product, action_type).
  - Expired and deactivated nudges are excluded from the constraint (partial
    WHERE status = 'active') so historical rows can accumulate without conflict.

On constraint violation, Postgres raises IntegrityError.  The nudge_engine's
create_or_refresh_nudge() catches this and retries with SELECT to return the
existing row — the same recovery path already used for the race-condition
handling in empirical_calibration.py.

Downgrade safety
----------------
Dropping the index reverts to service-layer-only enforcement.  No data is
lost — the constraint is additive only.
"""
from alembic import op


def upgrade() -> None:
    # CREATE UNIQUE INDEX CONCURRENTLY cannot run inside a transaction.
    # Alembic wraps upgrades in a transaction by default — we execute raw
    # DDL outside that transaction using op.execute() with CONCURRENTLY.
    # This is safe for PostgreSQL 9.6+ and does not lock the table.
    op.execute("""
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
            ix_active_nudges_unique_active
        ON active_nudges (shop_domain, product_url, action_type)
        WHERE status = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_active_nudges_unique_active")
