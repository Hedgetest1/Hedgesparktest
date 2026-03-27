"""merge two heads into single linear history

Revision ID: cc1_merge_heads
Revises: bb1_events_partitioning, u1b2c3d4e5f6
Create Date: 2026-03-24

Merges the two independent branches that diverged at s5e6f7a8b9c0:

  Branch A: s5e6f7a8b9c0 → t6f7a8b9c0d1 → u1b2c3d4e5f6
            (nudge A/B variants, holdout_pct)

  Branch B: s5e6f7a8b9c0 → v1a2b3c4d5e6 → ... → bb1_events_partitioning
            (shop_orders email, nudge impression dedup, merchant install/billing,
             GDPR pipeline, events partitioning)

No schema changes — this is a graph-only merge so all future migrations
chain from a single head.
"""
from alembic import op

revision = "cc1_merge_heads"
down_revision = ("bb1_events_partitioning", "u1b2c3d4e5f6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
