"""action_tasks: persistent action execution records

Revision ID: l1c2d3e4f5a6
Revises: k9a1b2c3d4e5
Create Date: 2026-03-21

Creates action_tasks — the persistence layer for the Action Execution System.

Each row represents one execution request for a (shop, product, action_type)
triple.  The table is append-only from the API layer: only the status,
executed_at, completed_at, and result_detail columns are ever updated after
insert.

JSONB columns (source_candidate, task_payload) require PostgreSQL 9.4+.
This project already uses PostgreSQL-specific syntax throughout.

Indexes
-------
  ix_action_tasks_shop_domain   — per-shop listing queries
  ix_action_tasks_status        — agent queue scan (WHERE status = 'pending')
  ix_action_tasks_shop_status   — per-shop pending queue (most common join)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "l1c2d3e4f5a6"
down_revision: Union[str, None] = "k9a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "action_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("product_url", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("triggered_by", sa.String(), nullable=False, server_default="manual"),
        sa.Column("source_candidate", postgresql.JSONB(), nullable=False),
        sa.Column("task_payload", postgresql.JSONB(), nullable=False),
        sa.Column("expected_loss", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("urgency", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result_detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_action_tasks_shop_domain",
        "action_tasks",
        ["shop_domain"],
    )
    op.create_index(
        "ix_action_tasks_status",
        "action_tasks",
        ["status"],
    )
    op.create_index(
        "ix_action_tasks_shop_status",
        "action_tasks",
        ["shop_domain", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_action_tasks_shop_status", table_name="action_tasks")
    op.drop_index("ix_action_tasks_status", table_name="action_tasks")
    op.drop_index("ix_action_tasks_shop_domain", table_name="action_tasks")
    op.drop_table("action_tasks")
