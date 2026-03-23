"""action_tasks: add claimed_by column for atomic agent claiming

Revision ID: m2d3e4f5a6b7
Revises: l1c2d3e4f5a6
Create Date: 2026-03-21

Adds claimed_by (nullable String) to action_tasks.

Set atomically during pending → executing transition using SELECT FOR UPDATE.
NULL means unclaimed.  A non-NULL value records which agent instance holds
the task, enabling crash detection and future stale-task recovery.

No index added — the existing ix_action_tasks_shop_status composite index
covers all queue-scan queries.  claimed_by is a detail column, not a filter.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m2d3e4f5a6b7"
down_revision: Union[str, None] = "l1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "action_tasks",
        sa.Column("claimed_by", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("action_tasks", "claimed_by")
