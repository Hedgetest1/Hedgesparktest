"""worker_state cleanup — add last_digest_date, drop dead last_success_at

Post-Stage-1 model-drift audit (2026-04-14). The audit_model_drift.py
sweep surfaced two orphan columns on worker_state that the
SQLAlchemy model did not declare:

  1. last_digest_date (VARCHAR(10)) — actively used by agent_worker
     via raw SQL to dedup the daily digest ("don't send more than
     one digest per day"). Exists in prod DB but was never added
     through a migration and never made it into the model. Fix:
     declare it as a proper Column on the WorkerState model so the
     raw SQL can eventually migrate to ORM, and add it via this
     migration with IF NOT EXISTS so the no-op on prod is safe.

  2. last_success_at (BIGINT) — declared in the prod DB, read only
     by scripts/verify_hardening.py, never written by any code.
     Always NULL across all 6 worker rows. BIGINT typing suggests
     someone meant epoch-ms but forgot to implement the writer.
     This is dead half-built state (§2 rule 7 — delete). Dropping
     is safe because:
        - no writer exists, so no data is lost
        - verify_hardening.py is updated in the same commit to not
          select this column
        - no test references it

The combination (add + drop) lives in ONE migration so the model
cleanup is atomic — a partial state where last_digest_date is
declared but last_success_at still lingers would confuse both the
audit script and future readers.

Revision ID: zzz6_worker_state_cleanup
Revises: zzz5_nudge_events_worker_log_composites
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = "zzz6_worker_state_cleanup"
down_revision = "zzz5_nudge_events_worker_log_composites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add last_digest_date if missing. Idempotent — prod already has
    # this column (added outside alembic history), a fresh dev env
    # does not.
    op.execute(
        """
        ALTER TABLE worker_state
        ADD COLUMN IF NOT EXISTS last_digest_date VARCHAR(10)
        """
    )

    # Drop the dead last_success_at column. Safe — no writer, always
    # NULL, only reader (verify_hardening.py) is updated in the same
    # commit to not select it.
    op.execute(
        """
        ALTER TABLE worker_state
        DROP COLUMN IF EXISTS last_success_at
        """
    )


def downgrade() -> None:
    # Re-add last_success_at as it was (BIGINT nullable) — the previous
    # state was a column that nothing wrote to, so the empty column is
    # equivalent to what used to exist.
    op.execute(
        """
        ALTER TABLE worker_state
        ADD COLUMN IF NOT EXISTS last_success_at BIGINT
        """
    )
    op.execute(
        """
        ALTER TABLE worker_state
        DROP COLUMN IF EXISTS last_digest_date
        """
    )
