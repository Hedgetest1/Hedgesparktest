"""merchants — add webhook and script_tag install tracking columns

Revision ID: y2d3e4f5a6b7
Revises: x1c2d3e4f5a6
Create Date: 2026-03-23

Adds four nullable columns to the merchants table to track the
auto-registration of the orders/paid webhook and spark-tracker.js
script tag that happen during the Shopify OAuth install callback.

Columns added
-------------
webhook_id              VARCHAR   — Shopify webhook ID (string; IDs exceed int32)
webhook_registered_at   DATETIME  — When the webhook was last successfully registered
script_tag_id           VARCHAR   — Shopify script tag ID
script_tag_installed_at DATETIME  — When the script tag was last successfully installed

All columns are nullable — NULL means not yet registered or last attempt failed.
No default values — these are populated explicitly by the install flow.

No data migration required — existing merchant rows simply get NULL values,
which correctly reflects their pre-automation status (manually configured).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "y2d3e4f5a6b7"
down_revision = "x1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("merchants",
        sa.Column("webhook_id",              sa.String(),   nullable=True))
    op.add_column("merchants",
        sa.Column("webhook_registered_at",   sa.DateTime(), nullable=True))
    op.add_column("merchants",
        sa.Column("script_tag_id",           sa.String(),   nullable=True))
    op.add_column("merchants",
        sa.Column("script_tag_installed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("merchants", "script_tag_installed_at")
    op.drop_column("merchants", "script_tag_id")
    op.drop_column("merchants", "webhook_registered_at")
    op.drop_column("merchants", "webhook_id")
