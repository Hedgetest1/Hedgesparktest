"""google_oauth_tokens — G4 Lite parity Google Sheets export

Revision ID: zzzf_google_oauth_tokens
Revises: zzze_survey_questions_array
Create Date: 2026-04-29

TIER_2 schema add — 3 nullable columns on `merchants` for Google
OAuth (drive.file scope) state. Per `feedback_settings_is_tier_agnostic_chrome.md`
the integration is Lite-accessible. Encrypted refresh_token via existing
`app/core/token_crypto.py` (AES-256-GCM, enc:v1: prefix).

Columns:
  - encrypted_google_refresh_token : TEXT — AES-256-GCM encrypted refresh
    token (Google OAuth flow returns refresh_token once on consent;
    access_token is short-lived and refreshed in-memory). NULL = not
    connected.
  - google_oauth_email : VARCHAR(255) — display only, the Google
    account the merchant authorized (e.g. "owner@brand.com"). Used to
    show "Connected as owner@brand.com · Disconnect" in the UI.
  - google_oauth_connected_at : TIMESTAMP — when the OAuth handshake
    completed. Used for staleness detection + audit log.
"""
from __future__ import annotations

from alembic import op


revision = "zzzf_google_oauth_tokens"
down_revision = "zzze_survey_questions_array"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE merchants
        ADD COLUMN IF NOT EXISTS encrypted_google_refresh_token TEXT NULL,
        ADD COLUMN IF NOT EXISTS google_oauth_email VARCHAR(255) NULL,
        ADD COLUMN IF NOT EXISTS google_oauth_connected_at TIMESTAMP NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE merchants
        DROP COLUMN IF EXISTS encrypted_google_refresh_token,
        DROP COLUMN IF EXISTS google_oauth_email,
        DROP COLUMN IF EXISTS google_oauth_connected_at;
        """
    )
