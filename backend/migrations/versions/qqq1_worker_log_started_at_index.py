"""Add index on worker_log.started_at for fast time-range queries.

The system_summary module queries worker_log WHERE started_at >= cutoff
on every /status and /costs command. Without this index, PostgreSQL
does a sequential scan on the full table (~10k rows).

Safe if index already exists (uses IF NOT EXISTS via raw SQL).

Revision ID: qqq1_worker_log_started_at_idx
Revises: ppp1_reviewer_links
Create Date: 2026-03-29
"""
from alembic import op

revision = "qqq1_worker_log_started_at_idx"
down_revision = "ppp1_reviewer_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_worker_log_started_at "
        "ON worker_log (started_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_worker_log_started_at")
