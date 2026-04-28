"""post_purchase_survey responses + merchant config

Revision ID: zzza_post_purchase_survey
Revises: ecb1659d2093
Create Date: 2026-04-28

TIER_2 — founder approved sprint-scope grant 2026-04-28 (Gap #7
of project_brutal_audit_0_70_2026_04_27.md).

Why this migration
------------------
Closes Gap #7 of the brutal $0-70 competitor audit: post-purchase
attribution survey ("how did you hear about us") deployed on
Thank-You + Order-Status pages via Shopify Checkout UI Extension.
Stack parity with KnoCommerce, Fairing, Zigpoll, Pathlight (free
tier of every competitor in the band ships this).

What this migration does
------------------------
1. New table `survey_responses`
   - One row per (shop, order, question_key) — UNIQUE constraint
     prevents double-submit across browser refreshes / dual-surface
     (Thank-You + Order-Status both fire and dedup).
   - No PII columns: `client_ip_hash` is sha256(ip + daily salt),
     `user_agent_hash` is sha256(ua); raw values never stored.
   - `answer_text` runs through llm_pii_guard at API boundary —
     PII-positive rows are stored with answer_text=NULL.
   - Index `idx_survey_responses_shop_created` covers the dashboard
     last-30d aggregate query path.
   - Partial index on `answer_choice WHERE NOT NULL` accelerates
     the GROUP BY aggregate.

2. 4 new columns on `merchants` table for Pro customization
   - `survey_question` (default question text)
   - `survey_options` (JSONB array of {label, value})
   - `survey_allow_other` (free-text fallback toggle)
   - `survey_show_on_order_status` (per-merchant Order-Status gate)

   Inline on merchants follows the existing pattern
   (Klaviyo, Slack are inline columns too — no separate
   merchant_settings table exists in this codebase).

GDPR
----
- Retention: 365d via retention_task.py (added in companion code)
- Art. 17 erasure: gdpr_processor.uninstall_erasure cascades by
  shop_domain (added in companion code)
- No PII collected; consent gate enforced client-side via
  shopify.customerPrivacy.consent.analyticsProcessingAllowed

Scale
-----
At 10k merchants × 100 orders/mo × 30% response rate =
~300k rows/mo → ~36MB/yr. Negligible. Index covers all 3 query
paths (aggregate, dedup, retention purge).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zzza_post_purchase_survey"
down_revision: Union[str, Sequence[str], None] = "ecb1659d2093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_OPTIONS_JSON = (
    '['
    '{"label":"Instagram","value":"instagram"},'
    '{"label":"TikTok","value":"tiktok"},'
    '{"label":"Google","value":"google"},'
    '{"label":"Friend","value":"friend"},'
    '{"label":"Email","value":"email"}'
    ']'
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. survey_responses table
    # ------------------------------------------------------------------
    op.create_table(
        "survey_responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column(
            "question_key",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'how_did_you_hear'"),
        ),
        sa.Column("answer_choice", sa.String(64), nullable=True),
        sa.Column("answer_text", sa.String(500), nullable=True),
        sa.Column(
            "consent_given",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # sha256(client_ip + daily_salt) — 64 hex chars
        sa.Column("client_ip_hash", sa.String(64), nullable=True),
        # sha256(user_agent) — 64 hex chars
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_survey_responses_shop_order_key",
        "survey_responses",
        ["shop_domain", "order_id", "question_key"],
    )
    op.create_index(
        "idx_survey_responses_shop_created",
        "survey_responses",
        ["shop_domain", sa.text("created_at DESC")],
    )
    # Partial index — only index rows that contributed to aggregate
    # (skips PII-rejected rows where answer_choice is NULL)
    op.create_index(
        "idx_survey_responses_shop_question_choice",
        "survey_responses",
        ["shop_domain", "question_key", "answer_choice"],
        postgresql_where=sa.text("answer_choice IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 2. merchants — inline survey config columns
    # ------------------------------------------------------------------
    op.add_column(
        "merchants",
        sa.Column(
            "survey_question",
            sa.String(160),
            nullable=False,
            server_default=sa.text("'How did you hear about us?'"),
        ),
    )
    op.add_column(
        "merchants",
        sa.Column(
            "survey_options",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_OPTIONS_JSON}'::jsonb"),
        ),
    )
    op.add_column(
        "merchants",
        sa.Column(
            "survey_allow_other",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "merchants",
        sa.Column(
            "survey_show_on_order_status",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("merchants", "survey_show_on_order_status")
    op.drop_column("merchants", "survey_allow_other")
    op.drop_column("merchants", "survey_options")
    op.drop_column("merchants", "survey_question")

    op.drop_index(
        "idx_survey_responses_shop_question_choice",
        table_name="survey_responses",
    )
    op.drop_index(
        "idx_survey_responses_shop_created",
        table_name="survey_responses",
    )
    op.drop_constraint(
        "uq_survey_responses_shop_order_key",
        "survey_responses",
        type_="unique",
    )
    op.drop_table("survey_responses")
