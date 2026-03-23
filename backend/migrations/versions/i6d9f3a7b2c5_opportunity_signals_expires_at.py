"""fix: add expires_at to opportunity_signals, extend TTL to 24 hours

Revision ID: i6d9f3a7b2c5
Revises: h5c8e2f6a1b4
Create Date: 2026-03-19

Problem
-------
opportunity_signals were cleaned up after 1 hour (via refreshed_at + _STALE_HOURS
comparison in _persist_signals).  daily_brief reads signals to produce the brief
headline and metrics snapshot — with a 1-hour TTL, any shop whose detection engine
had not run in the last hour would receive an empty brief, regardless of how much
real signal activity existed for that shop.

Solution
--------
Add an explicit expires_at column (TIMESTAMP NOT NULL) to opportunity_signals.

  - expires_at is set to now() + SIGNAL_TTL_HOURS (24 h) on every INSERT or UPDATE.
  - The cleanup DELETE is now:  DELETE FROM opportunity_signals WHERE expires_at < now()
  - brief_engine reads:  WHERE detected_at >= now() - interval '24 hours'
                           AND expires_at  >= now()
  - The cleanup runs in aggregation_worker every cycle (cheap indexed range delete),
    not inline in _persist_signals, decoupling signal lifetime from detection frequency.

Schema changes
--------------
  opportunity_signals:
    ADD COLUMN expires_at TIMESTAMP NOT NULL DEFAULT now() + INTERVAL '24 hours'
    ADD INDEX  ix_opportunity_signals_expires_at (expires_at)

Migration of existing rows
--------------------------
Existing rows receive expires_at = NOW() + 24 hours via the column DEFAULT,
giving them a full grace period before the next cleanup cycle removes them.
Rows that are truly stale will be removed by the first cleanup run after deploy.
"""

from alembic import op
import sqlalchemy as sa


revision = "i6d9f3a7b2c5"
down_revision = "h5c8e2f6a1b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add expires_at with a server_default so existing rows get a valid value.
    # DEFAULT (now() + interval '24 hours') gives all existing rows a full 24-hour
    # grace period, after which the aggregation_worker cleanup will remove them.
    op.add_column(
        "opportunity_signals",
        sa.Column(
            "expires_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )

    # Index to support both the cleanup DELETE and the brief_engine SELECT.
    op.create_index(
        "ix_opportunity_signals_expires_at",
        "opportunity_signals",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunity_signals_expires_at",
        table_name="opportunity_signals",
    )
    op.drop_column("opportunity_signals", "expires_at")
