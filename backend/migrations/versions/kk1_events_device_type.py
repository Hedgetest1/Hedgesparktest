"""Add device_type column to events table.

Stores "mobile" or "desktop" per event. Nullable for events ingested before
this migration. Populated by spark-tracker.js v3 via navigator.userAgent check.

Enables per-product device segmentation in the Action Engine.
"""

from alembic import op
import sqlalchemy as sa

revision = "kk1_events_device_type"
down_revision = "jj1_action_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("device_type", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "device_type")
