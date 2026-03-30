"""Add UTM and click ID columns to events for server-side attribution.

New columns on events:
  - utm_source (e.g., google, facebook, newsletter)
  - utm_campaign (campaign name)
  - utm_content (ad variant)
  - utm_term (search keyword)
  - click_id (gclid, fbclid, ttclid, msclkid — stored as single field with type prefix)
  - landing_page (first page URL of this visit)

New columns on visitor_purchase_sessions:
  - first_source, first_campaign (first-touch attribution snapshot at conversion time)
  - last_source, last_campaign (last-touch attribution snapshot at conversion time)
  - attribution_evidence (JSON: full attribution chain for audit)

All nullable — backward compatible with existing events.

Revision ID: uuu1_attribution_columns
Revises: ttt1_meta_reviews
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "uuu1_attribution_columns"
down_revision = "ttt1_meta_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Events: UTM parameters and click IDs
    op.add_column("events", sa.Column("utm_source", sa.String(128), nullable=True))
    op.add_column("events", sa.Column("utm_campaign", sa.String(256), nullable=True))
    op.add_column("events", sa.Column("utm_content", sa.String(256), nullable=True))
    op.add_column("events", sa.Column("utm_term", sa.String(256), nullable=True))
    op.add_column("events", sa.Column("click_id", sa.String(256), nullable=True))
    op.add_column("events", sa.Column("landing_page", sa.String(512), nullable=True))

    # Visitor purchase sessions: attribution snapshot at conversion time
    op.add_column("visitor_purchase_sessions", sa.Column("first_source", sa.String(64), nullable=True))
    op.add_column("visitor_purchase_sessions", sa.Column("first_campaign", sa.String(256), nullable=True))
    op.add_column("visitor_purchase_sessions", sa.Column("last_source", sa.String(64), nullable=True))
    op.add_column("visitor_purchase_sessions", sa.Column("last_campaign", sa.String(256), nullable=True))
    op.add_column("visitor_purchase_sessions", sa.Column("attribution_evidence", sa.Text, nullable=True))

    # Index for campaign-level attribution queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_events_shop_campaign "
        "ON events (shop_domain, utm_campaign) WHERE utm_campaign IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_events_shop_campaign")
    op.drop_column("visitor_purchase_sessions", "attribution_evidence")
    op.drop_column("visitor_purchase_sessions", "last_campaign")
    op.drop_column("visitor_purchase_sessions", "last_source")
    op.drop_column("visitor_purchase_sessions", "first_campaign")
    op.drop_column("visitor_purchase_sessions", "first_source")
    op.drop_column("events", "landing_page")
    op.drop_column("events", "click_id")
    op.drop_column("events", "utm_term")
    op.drop_column("events", "utm_content")
    op.drop_column("events", "utm_campaign")
    op.drop_column("events", "utm_source")
