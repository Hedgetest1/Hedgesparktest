"""Add GC lifecycle columns to evolution_proposals.

Adds gc_reason (text) and gc_updated_at (datetime) for garbage collector
audit trail. Widens status column from 16 to 32 chars to accommodate
new GC statuses: obsolete, resolved_indirectly, needs_revalidation.

Revision ID: nnn1_evolution_gc_lifecycle
Revises: mmm1_scaling_intelligence
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = "nnn1_evolution_gc_lifecycle"
down_revision = "mmm1_scaling_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolution_proposals", sa.Column("gc_reason", sa.Text(), nullable=True))
    op.add_column("evolution_proposals", sa.Column("gc_updated_at", sa.DateTime(), nullable=True))
    # Widen status column for new GC statuses (resolved_indirectly = 21 chars)
    op.alter_column("evolution_proposals", "status",
                    existing_type=sa.String(16),
                    type_=sa.String(32),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column("evolution_proposals", "status",
                    existing_type=sa.String(32),
                    type_=sa.String(16),
                    existing_nullable=False)
    op.drop_column("evolution_proposals", "gc_updated_at")
    op.drop_column("evolution_proposals", "gc_reason")
