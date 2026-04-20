"""slack integration columns on merchants

Strada 3.5 (2026-04-20). Adds three columns to merchants for the
per-merchant Slack webhook integration:

  slack_webhook_encrypted  TEXT NULLABLE    — AES-256-GCM(webhook URL)
  slack_status             TEXT NOT NULL
                           DEFAULT 'not_connected'
                           — 'connected' | 'error' | 'not_connected'
  slack_last_error         TEXT NULLABLE    — sanitized last error

Pattern mirrors the existing encrypted_klaviyo_key + klaviyo_*
columns so ops visibility (status / last_error) works the same way.

Revision ID: aaa0_slack_integration
Revises: zzz9_prediction_log
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "aaa0_slack_integration"
down_revision: Union[str, Sequence[str], None] = "zzz9_prediction_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "merchants",
        sa.Column("slack_webhook_encrypted", sa.String(), nullable=True),
    )
    op.add_column(
        "merchants",
        sa.Column(
            "slack_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_connected",
        ),
    )
    op.add_column(
        "merchants",
        sa.Column("slack_last_error", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchants", "slack_last_error")
    op.drop_column("merchants", "slack_status")
    op.drop_column("merchants", "slack_webhook_encrypted")
