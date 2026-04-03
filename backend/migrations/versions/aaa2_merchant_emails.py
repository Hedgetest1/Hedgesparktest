"""merchant emails audit log

Revision ID: aaa2_merchant_emails
Revises: zzz1_onboarding_funnel_events
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "aaa2_merchant_emails"
down_revision = "zzz1_onboarding_funnel_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_emails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("shop_domain", sa.String(), nullable=False),
        sa.Column("email_type", sa.String(64), nullable=False),
        sa.Column("to_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resend_id", sa.String(128), nullable=True),
        sa.Column("suppressed_by", sa.String(128), nullable=True),
    )
    op.create_index("ix_merchant_emails_shop", "merchant_emails", ["shop_domain"])
    op.create_index("ix_merchant_emails_type", "merchant_emails", ["email_type"])
    op.create_index("ix_merchant_emails_shop_type", "merchant_emails", ["shop_domain", "email_type"])
    op.create_index("ix_merchant_emails_created", "merchant_emails", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_merchant_emails_created", "merchant_emails")
    op.drop_index("ix_merchant_emails_shop_type", "merchant_emails")
    op.drop_index("ix_merchant_emails_type", "merchant_emails")
    op.drop_index("ix_merchant_emails_shop", "merchant_emails")
    op.drop_table("merchant_emails")
