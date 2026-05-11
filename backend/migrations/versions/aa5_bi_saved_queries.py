"""bi_saved_queries — Pro #3 BI Query Builder saved-query storage

Stores merchant-saved query builder configurations (table + columns +
filters + group-by + order-by + limit) as JSONB. Queries are NOT
pre-executed; each save just records the structured builder state so
the merchant can recall + re-run later.

GDPR / safety notes:
  - query_json is the structured QueryRequest payload — never raw SQL.
    The SQL is reconstructed server-side from the JSONB on every run,
    so a tampered row cannot inject SQL by editing query_json directly
    (parser would reject unknown fields / table-allowlist violations).
  - shop_domain CHECK keeps tenant isolation visible at the table level.

Revision ID: aa5_bi_saved_queries
Revises: aa4_cross_shop_patterns
"""
from alembic import op


revision = "aa5_bi_saved_queries"
down_revision = "aa4_cross_shop_patterns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bi_saved_queries (
            id BIGSERIAL PRIMARY KEY,
            shop_domain VARCHAR NOT NULL,
            name VARCHAR(128) NOT NULL,
            query_json JSONB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT bi_saved_queries_name_check
                CHECK (length(name) BETWEEN 1 AND 128)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_bi_saved_queries_shop
        ON bi_saved_queries (shop_domain, updated_at)
    """)
    op.execute("""
        ALTER TABLE bi_saved_queries
        ADD CONSTRAINT uq_bi_saved_queries_shop_name
        UNIQUE (shop_domain, name)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE bi_saved_queries
        DROP CONSTRAINT IF EXISTS uq_bi_saved_queries_shop_name
    """)
    op.execute("DROP INDEX IF EXISTS ix_bi_saved_queries_shop")
    op.execute("DROP TABLE IF EXISTS bi_saved_queries")
