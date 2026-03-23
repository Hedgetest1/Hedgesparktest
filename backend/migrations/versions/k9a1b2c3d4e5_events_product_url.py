"""feat: add product_url column to events table

Revision ID: k9a1b2c3d4e5
Revises: j7e0a4b8c3d6
Create Date: 2026-03-20

Problem
-------
The events table has a single `url` column that has been used to store both
the raw page URL and the product URL, merged at ingestion time:

    url = payload.product_url or payload.page_url

This conflation makes it impossible to:
  - Distinguish which events happened on a product page vs a generic page.
  - Filter analytics (e.g. /analytics/source-quality) accurately per product.
  - Attach product context to scroll/dwell events separately from the page URL.

Both trackers (spark-tracker.js and dashboard/public/tracker.js) already send
product_url on every event payload. The field was accepted by track.py but
discarded into the single url column, losing the product context.

Solution
--------
Add a dedicated `product_url` column (nullable VARCHAR) to events:
  - product_url: the canonical product path when the event fired on a product
                 page; NULL for events fired on non-product pages.
  - url: will now always store the raw page_url from the tracker.

Both are nullable. Historical rows are backfilled:
  - Rows where url matches '%/products/%' were stored from payload.product_url
    (because of the "or" priority in old ingestion logic). We recover this by
    copying url → product_url for those rows.
  - All other historical rows correctly have product_url = NULL.

Schema changes
--------------
  events:
    ADD COLUMN product_url  VARCHAR  NULL
    ADD INDEX  ix_events_shop_product_url  (shop_domain, product_url)
      — supports GROUP BY / WHERE in /analytics/source-quality

Backfill
--------
  UPDATE events SET product_url = url WHERE url LIKE '%/products/%'

  This is safe: the old ingestion logic stored product_url into url for
  product pages, so those rows contain the product path in url.
"""

from alembic import op
import sqlalchemy as sa


revision = "k9a1b2c3d4e5"
down_revision = "j7e0a4b8c3d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the dedicated product_url column.
    # Nullable: non-product-page events and pre-migration rows have no product context.
    op.add_column(
        "events",
        sa.Column("product_url", sa.String(), nullable=True),
    )

    # Composite index — keeps WHERE product_url = :x AND shop_domain = :y fast.
    op.create_index(
        "ix_events_shop_product_url",
        "events",
        ["shop_domain", "product_url"],
    )

    # Backfill: recover product_url from url for historical product-page events.
    # Old ingestion: url = payload.product_url or payload.page_url, so rows
    # where url contains '/products/' were stored from the product_url field.
    op.execute(
        "UPDATE events SET product_url = url WHERE url LIKE '%/products/%'"
    )


def downgrade() -> None:
    op.drop_index("ix_events_shop_product_url", table_name="events")
    op.drop_column("events", "product_url")
