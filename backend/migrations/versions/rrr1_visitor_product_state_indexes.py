"""Add indexes on visitor_product_state for intent query performance.

Core query patterns:
  - Per-shop product intent: WHERE shop_domain = ? AND product_url = ?
  - Per-visitor engagement: WHERE shop_domain = ? AND visitor_id = ?

Without indexes, these degrade to sequential scans as the table grows.

Safe: uses IF NOT EXISTS for idempotency.

Revision ID: rrr1_visitor_product_state_idx
Revises: qqq1_worker_log_started_at_idx
Create Date: 2026-03-30
"""
from alembic import op

revision = "rrr1_visitor_product_state_idx"
down_revision = "qqq1_worker_log_started_at_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vps_shop_product "
        "ON visitor_product_state (shop_domain, product_url)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vps_shop_visitor "
        "ON visitor_product_state (shop_domain, visitor_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vps_shop_product")
    op.execute("DROP INDEX IF EXISTS ix_vps_shop_visitor")
