"""Add Klaviyo connection fields to merchants table.

Enables per-merchant Klaviyo credential storage (encrypted at rest)
and structured connection state for AI-manageable integration status.

Revision ID: tt1_merchant_klaviyo_connection
Revises: ss1_eligibility_index
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "tt1_merchant_klaviyo_connection"
down_revision = "ss1_eligibility_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Encrypted Klaviyo private key (enc:v1:... format, same as access_token)
    op.add_column(
        "merchants",
        sa.Column("encrypted_klaviyo_key", sa.String(), nullable=True),
    )

    # Structured connection state — AI-inspectable
    op.add_column(
        "merchants",
        sa.Column(
            "klaviyo_connection_status",
            sa.String(32),
            nullable=False,
            server_default="not_connected",
        ),
    )
    op.add_column(
        "merchants",
        sa.Column("klaviyo_last_verified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column("klaviyo_last_error", sa.String(255), nullable=True),
    )

    # Sync observability — lets AI inspect last sync attempt outcome
    op.add_column(
        "merchants",
        sa.Column("klaviyo_last_sync_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column("klaviyo_last_sync_error", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "klaviyo_last_sync_error")
    op.drop_column("merchants", "klaviyo_last_sync_at")
    op.drop_column("merchants", "klaviyo_last_error")
    op.drop_column("merchants", "klaviyo_last_verified_at")
    op.drop_column("merchants", "klaviyo_connection_status")
    op.drop_column("merchants", "encrypted_klaviyo_key")
