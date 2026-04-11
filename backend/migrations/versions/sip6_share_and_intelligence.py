"""share system + intelligence report tables

Revision ID: sip6_share_and_intelligence
Revises: sip5_commerce_intelligence_graph
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "sip6_share_and_intelligence"
down_revision = "sip5_commerce_intelligence_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_proof_shares",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_domain", sa.String, nullable=False, index=True),
        sa.Column("share_token", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("proof_type", sa.String(32), nullable=False),
        sa.Column("nudge_id", sa.Integer, nullable=True),
        sa.Column("proof_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("headline", sa.String(256), nullable=False),
        sa.Column("twitter_text", sa.String(512), nullable=True),
        sa.Column("generic_text", sa.String(512), nullable=True),
        sa.Column("view_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("click_cta_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("installs_attributed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "share_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("share_token", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("referrer", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("share_events")
    op.drop_table("public_proof_shares")
