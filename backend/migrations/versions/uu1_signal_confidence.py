"""Add signal_confidence column to opportunity_signals.

Enables three-tier confidence model:
  'high'   — strong data, existing thresholds (20+ views, etc.)
  'medium' — moderate data, mid-range thresholds
  'low'    — early signals from minimal data (1-5 visitors)

Low-confidence signals are excluded from Klaviyo automation
but shown in the dashboard for immediate time-to-value.

Revision ID: uu1_signal_confidence
Revises: tt1_merchant_klaviyo_connection
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "uu1_signal_confidence"
down_revision = "tt1_merchant_klaviyo_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunity_signals",
        sa.Column(
            "signal_confidence",
            sa.String(16),
            nullable=False,
            server_default="high",
        ),
    )


def downgrade() -> None:
    op.drop_column("opportunity_signals", "signal_confidence")
