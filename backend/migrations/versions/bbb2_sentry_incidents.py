"""sentry incident triage table

Revision ID: bbb2_sentry_incidents
Revises: aaa2_merchant_emails
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "bbb2_sentry_incidents"
down_revision = "aaa2_merchant_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentry_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),

        # Source
        sa.Column("source_message_id", sa.String(256), nullable=True, unique=True),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="email"),

        # Raw
        sa.Column("raw_subject", sa.String(512), nullable=True),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("raw_from", sa.String(256), nullable=True),
        sa.Column("raw_to", sa.String(256), nullable=True),

        # Parsed
        sa.Column("error_type", sa.String(256), nullable=True),
        sa.Column("error_title", sa.String(512), nullable=True),
        sa.Column("project", sa.String(128), nullable=True),
        sa.Column("environment", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(32), nullable=True),
        sa.Column("culprit", sa.String(512), nullable=True),
        sa.Column("stack_trace", sa.Text(), nullable=True),
        sa.Column("sentry_issue_url", sa.String(512), nullable=True),

        # Fingerprint
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("fingerprint_input", sa.String(512), nullable=True),

        # Grouping
        sa.Column("family_head_id", sa.Integer(), nullable=True),
        sa.Column("recurrence_count", sa.Integer(), nullable=False, server_default="1"),

        # Triage
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("parse_error", sa.String(512), nullable=True),

        # AI triage
        sa.Column("triage_packet", sa.Text(), nullable=True),
        sa.Column("ai_triage_status", sa.String(32), nullable=True),

        # Integration
        sa.Column("linked_bugfix_candidate_id", sa.Integer(), nullable=True),
        sa.Column("linked_ops_alert_id", sa.Integer(), nullable=True),
        sa.Column("lesson_candidate_status", sa.String(32), nullable=True),
    )
    op.create_index("ix_sentry_incidents_fingerprint", "sentry_incidents", ["fingerprint"])
    op.create_index("ix_sentry_incidents_status", "sentry_incidents", ["status"])
    op.create_index("ix_sentry_incidents_created", "sentry_incidents", ["created_at"])
    op.create_index("ix_sentry_incidents_family", "sentry_incidents", ["family_head_id"])
    op.create_index("ix_sentry_incidents_ai_status", "sentry_incidents", ["ai_triage_status"])


def downgrade() -> None:
    op.drop_index("ix_sentry_incidents_ai_status", "sentry_incidents")
    op.drop_index("ix_sentry_incidents_family", "sentry_incidents")
    op.drop_index("ix_sentry_incidents_created", "sentry_incidents")
    op.drop_index("ix_sentry_incidents_status", "sentry_incidents")
    op.drop_index("ix_sentry_incidents_fingerprint", "sentry_incidents")
    op.drop_table("sentry_incidents")
