"""merchants — billing and uninstall lifecycle columns

Revision ID: z1a2b3c4d5e6
Revises: y2d3e4f5a6b7
Create Date: 2026-03-23

Adds lifecycle and billing tracking columns to merchants:

install_status VARCHAR (default 'active')
    "active"      — app installed and operational
    "uninstalled" — merchant removed the app

uninstalled_at DATETIME (nullable)
    Timestamp of the last app/uninstalled webhook received.

billing_charge_id VARCHAR (nullable)
    Shopify RecurringApplicationCharge.id. Set when a billing request
    is initiated (pending), retained on activation, cleared on decline.

billing_confirmed_at DATETIME (nullable)
    When the charge was accepted and activated via Shopify billing API.

Existing rows:
    install_status gets default value 'active' (correct — they are installed).
    All other columns are nullable and default to NULL.
    No data backfill required.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "z1a2b3c4d5e6"
down_revision = "y2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("merchants",
        sa.Column("install_status",       sa.String(),   nullable=False,
                  server_default="active"))
    op.add_column("merchants",
        sa.Column("uninstalled_at",       sa.DateTime(), nullable=True))
    op.add_column("merchants",
        sa.Column("billing_charge_id",    sa.String(),   nullable=True))
    op.add_column("merchants",
        sa.Column("billing_confirmed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("merchants", "billing_confirmed_at")
    op.drop_column("merchants", "billing_charge_id")
    op.drop_column("merchants", "uninstalled_at")
    op.drop_column("merchants", "install_status")
