"""phase1: add events(shop_domain, timestamp DESC) index

Revision ID: f3a6c9b4d2e8
Revises: e2f5b8a3c1d7
Create Date: 2026-03-18

Adds a composite index on events(shop_domain, timestamp DESC).

This index is the single highest-leverage change in Phase 1.  Every
time-bounded per-shop query — aggregation worker, detection engine,
live-visitors endpoint, top-pages endpoint — performs a full
sequential scan on the events table without it.

WHY THIS IS A SEPARATE MIGRATION
---------------------------------
CREATE INDEX CONCURRENTLY cannot execute inside a transaction.
Alembic wraps all migrations in a transaction by default.  This
migration explicitly commits the implicit transaction before running
the CONCURRENTLY statement, then lets Alembic open a new implicit
transaction for the alembic_version update.

The IF NOT EXISTS / IF EXISTS guards make both upgrade() and
downgrade() idempotent — safe to retry if the process is interrupted
mid-execution.

RECOVERY IF THIS MIGRATION FAILS MID-WAY
-----------------------------------------
PostgreSQL marks a CONCURRENTLY index as INVALID if the build is
interrupted.  To clean up:

    DROP INDEX CONCURRENTLY IF EXISTS ix_events_shop_domain_timestamp;

Then re-run:

    alembic upgrade head

The IF NOT EXISTS guard ensures the next run rebuilds cleanly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a6c9b4d2e8"
down_revision: Union[str, None] = "e2f5b8a3c1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_events_shop_domain_timestamp"


def upgrade() -> None:
    # Exit the implicit transaction — CONCURRENTLY requires no active
    # transaction in PostgreSQL.  Alembic will start a new implicit
    # transaction for the alembic_version stamp after this function returns.
    op.execute(sa.text("COMMIT"))

    op.execute(
        sa.text(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME} "
            "ON events (shop_domain, timestamp DESC)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("COMMIT"))

    op.execute(
        sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
    )
