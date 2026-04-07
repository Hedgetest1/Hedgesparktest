"""Add evidence_source column to learning artifact tables.

Learning isolation: classifies whether evidence came from pre_merchant,
internal_test, sandbox, or real_merchant. Only real_merchant evidence
may influence product reasoning (confidence boosts, reinforcement weights,
strategic memory).

Revision ID: ppp1_learning_isolation
Revises: ooo1_active_nudges_ai_compose_pending
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "ppp1_learning_isolation"
down_revision = "ooo1_active_nudges_ai_compose_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Default to 'pre_merchant' — all existing data is pre-merchant.
    for table in [
        "bugfix_candidates",
        "system_lessons",
        "patch_fingerprints",
        "evolution_proposals",
    ]:
        op.add_column(
            table,
            sa.Column(
                "evidence_source",
                sa.String(32),
                nullable=True,
                server_default="pre_merchant",
            ),
        )


def downgrade() -> None:
    for table in [
        "bugfix_candidates",
        "system_lessons",
        "patch_fingerprints",
        "evolution_proposals",
    ]:
        op.drop_column(table, "evidence_source")
