"""gdpr_requests table for GDPR deletion pipeline

Revision ID: aa1_gdpr_requests
Revises: z1a2b3c4d5e6
Create Date: 2026-03-24

Tracks incoming Shopify GDPR webhooks as jobs for the gdpr_worker.

Idempotent: safe to run when Base.metadata.create_all has pre-created the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "aa1_gdpr_requests"
down_revision = "z1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    existing_tables = inspector.get_table_names()

    if "gdpr_requests" not in existing_tables:
        op.create_table(
            "gdpr_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("request_type", sa.String(), nullable=False),
            sa.Column("shop_domain", sa.String(), nullable=False),
            sa.Column("customer_id", sa.String(), nullable=True),
            sa.Column("customer_email", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
        )

    existing_indexes = [i["name"] for i in inspector.get_indexes("gdpr_requests")]
    if "ix_gdpr_requests_shop_status" not in existing_indexes:
        op.create_index("ix_gdpr_requests_shop_status", "gdpr_requests", ["shop_domain", "status"])
    if "ix_gdpr_requests_created" not in existing_indexes:
        op.create_index("ix_gdpr_requests_created", "gdpr_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_gdpr_requests_created", table_name="gdpr_requests")
    op.drop_index("ix_gdpr_requests_shop_status", table_name="gdpr_requests")
    op.drop_table("gdpr_requests")
