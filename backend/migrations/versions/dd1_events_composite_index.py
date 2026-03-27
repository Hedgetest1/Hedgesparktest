"""events: add composite index (shop_domain, product_url, timestamp)

Revision ID: dd1_events_composite_index
Revises: cc1_merge_heads
Create Date: 2026-03-24

The aggregation worker's per-product metric CTE filters on all three columns:
  WHERE shop_domain = :shop AND product_url = :product AND timestamp >= :cutoff

The existing indexes cover (shop_domain, timestamp) and (shop_domain, product_url)
separately.  This 3-column composite eliminates the index scan + filter pattern
and gives the planner a single covering index for the most frequent query path.

Idempotent: uses IF NOT EXISTS.
"""
from alembic import op
import sqlalchemy as sa

revision = "dd1_events_composite_index"
down_revision = "cc1_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_events_shop_product_ts
        ON events (shop_domain, product_url, timestamp DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DROP INDEX IF EXISTS ix_events_shop_product_ts
    """))
