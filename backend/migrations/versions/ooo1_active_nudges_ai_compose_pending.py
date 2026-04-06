"""Add ai_compose_pending flag to active_nudges.

When a merchant creates a Pro nudge through the dashboard, the backend
was blocking the request on an OpenAI call (up to 20s). This column
lets the POST endpoint return IMMEDIATELY with a deterministic baseline
nudge, while a background worker upgrades it to AI-composed variants
within the next 5-minute aggregation cycle.

Default NULL/False. Existing nudges unaffected.

Revision ID: ooo1_active_nudges_ai_compose_pending
Revises: nnn1_evolution_strategy_alignment
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "ooo1_active_nudges_ai_compose_pending"
down_revision = "nnn1_evolution_strategy_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("active_nudges", sa.Column("ai_compose_pending", sa.Boolean(), nullable=True))
    op.create_index(
        "ix_active_nudges_ai_compose_pending",
        "active_nudges",
        ["ai_compose_pending"],
    )


def downgrade() -> None:
    op.drop_index("ix_active_nudges_ai_compose_pending", table_name="active_nudges")
    op.drop_column("active_nudges", "ai_compose_pending")
