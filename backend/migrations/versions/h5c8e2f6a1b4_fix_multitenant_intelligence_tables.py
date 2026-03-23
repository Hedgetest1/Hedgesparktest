from alembic import op

revision = "h5c8e2f6a1b4"
down_revision = "g4b7d1e5f0a2"
branch_labels = None
depends_on = None


_CREATE_UNIQUE_IF_MISSING = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = '{table}'
          AND c.conname = '{constraint}'
    ) THEN
        ALTER TABLE {table}
        ADD CONSTRAINT {constraint}
        UNIQUE (shop_domain, product_url);
    END IF;
END $$;
"""

_DROP_UNIQUE_IF_EXISTS = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t     ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname   = current_schema()
          AND t.relname   = '{table}'
          AND c.conname   = '{constraint}'
          AND c.contype   = 'u'
    ) THEN
        ALTER TABLE {table} DROP CONSTRAINT {constraint};
    END IF;
END $$;
"""


def _add_unique(table: str, constraint: str) -> None:
    op.execute(
        _CREATE_UNIQUE_IF_MISSING.format(table=table, constraint=constraint)
    )


def _drop_unique(table: str, constraint: str) -> None:
    op.execute(
        _DROP_UNIQUE_IF_EXISTS.format(table=table, constraint=constraint)
    )


def upgrade() -> None:
    op.create_index(
        "ix_product_opportunities_shop_domain",
        "product_opportunities",
        ["shop_domain"],
        if_not_exists=True,
    )
    _add_unique("product_opportunities", "uq_product_opportunities_shop_product")

    op.create_index(
        "ix_price_intelligence_shop_domain",
        "price_intelligence",
        ["shop_domain"],
        if_not_exists=True,
    )
    _add_unique("price_intelligence", "uq_price_intelligence_shop_product")

    op.create_index(
        "ix_unique_product_detection_shop_domain",
        "unique_product_detection",
        ["shop_domain"],
        if_not_exists=True,
    )
    _add_unique("unique_product_detection", "uq_unique_product_detection_shop_product")

    op.create_index(
        "ix_market_lookup_shop_domain",
        "market_lookup",
        ["shop_domain"],
        if_not_exists=True,
    )
    _add_unique("market_lookup", "uq_market_lookup_shop_product")


def downgrade() -> None:
    _drop_unique("market_lookup", "uq_market_lookup_shop_product")
    op.drop_index(
        "ix_market_lookup_shop_domain",
        table_name="market_lookup",
        if_exists=True,
    )

    _drop_unique("unique_product_detection", "uq_unique_product_detection_shop_product")
    op.drop_index(
        "ix_unique_product_detection_shop_domain",
        table_name="unique_product_detection",
        if_exists=True,
    )

    _drop_unique("price_intelligence", "uq_price_intelligence_shop_product")
    op.drop_index(
        "ix_price_intelligence_shop_domain",
        table_name="price_intelligence",
        if_exists=True,
    )

    _drop_unique("product_opportunities", "uq_product_opportunities_shop_product")
    op.drop_index(
        "ix_product_opportunities_shop_domain",
        table_name="product_opportunities",
        if_exists=True,
    )
