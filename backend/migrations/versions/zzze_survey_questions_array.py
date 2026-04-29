"""survey_questions array — G3 Lite parity multi-question survey

Revision ID: zzze_survey_questions_array
Revises: zzzd_merchant_groups
Create Date: 2026-04-29

TIER_2 schema add — single new nullable JSONB column on `merchants`.
Backward-compatible: when NULL, all existing single-question logic
applies unchanged (`survey_question` + `survey_options` columns).
When set, survey config endpoint returns the array of questions.

Question shape (validated at API boundary, not DB):
    {
      "question_key": "string<=64",
      "question": "string<=160",
      "type": "single_choice" | "multi_choice" | "text" | "nps",
      "options": [{"label": "...", "value": "..."}, ...],   // optional for text/nps
      "allow_other": bool,
      "position": int   // ordering, 0-based
    }

Lite parity: KnoCommerce Free, Zigpoll Free, Fairing $15 all ship
multi-question post-purchase surveys.
"""
from __future__ import annotations

from alembic import op


revision = "zzze_survey_questions_array"
down_revision = "zzzd_merchant_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE merchants
        ADD COLUMN IF NOT EXISTS survey_questions JSONB NULL;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE merchants DROP COLUMN IF EXISTS survey_questions;")
