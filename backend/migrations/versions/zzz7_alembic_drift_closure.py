"""alembic drift closure — eliminate the last 9 model/DB drift items

Tier 1.2 of the top-1 hardening roadmap. After every model edit landed,
`alembic check` still reports 9 mismatches that require real DDL on the
prod DB — they cannot be resolved by editing models alone. This migration
closes them all so `alembic check` exits 0.

The 9 items:

1-2. evolution_proposals.{infra_cost_estimate,impact_radius}
     VARCHAR(16) → VARCHAR(32). The model widened these fields months ago
     but no migration shipped. Widening is non-destructive (same data fits).

3-6. DROP duplicate UNIQUE CONSTRAINTS on the *_shop_product tables.
     Each of market_lookup, price_intelligence, product_opportunities, and
     unique_product_detection has TWO unique constraints with identical
     column lists:
        uq_<table>_shop_product             (kept — declared by model)
        uq_<table>_shop_domain_product_url  (dropped — redundant)
     Some old migration created the long-named one and a later one
     created the short-named one; both ended up in prod. The model only
     declares the short name, so the long name is the one to drop.

7. DROP CONSTRAINT uq_merchants_shop_domain on merchants.
   The model declares `unique=True, index=True` on shop_domain which
   produces the unique index `ix_merchants_shop_domain`. The separate
   `uq_merchants_shop_domain` constraint duplicates it and is dropped.

8. DROP INDEX ix_store_metrics_shop on store_metrics.
   The model declares `unique=True` on shop_domain which auto-creates
   `store_metrics_shop_domain_key` (Postgres default). The manual
   `ix_store_metrics_shop` unique index duplicates it and is dropped.

9. CREATE INDEX ix_vps_shop_visitor on visitor_product_state
   (shop_domain, visitor_id). The model declares it but the DB never had
   it. Created CONCURRENTLY-equivalent: this table is small enough that
   a regular CREATE INDEX is fine, but we add IF NOT EXISTS for safety.

Idempotency: every operation uses IF EXISTS / IF NOT EXISTS so re-running
on a partially-applied DB is safe.
"""
from alembic import op


revision = "zzz7_alembic_drift_closure"
down_revision = "zzz6_worker_state_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1-2. evolution_proposals VARCHAR widening (16 → 32).
    op.execute(
        "ALTER TABLE evolution_proposals "
        "ALTER COLUMN infra_cost_estimate TYPE VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE evolution_proposals "
        "ALTER COLUMN impact_radius TYPE VARCHAR(32)"
    )

    # 3-6. Drop duplicate unique constraints on the *_shop_product tables.
    for table, constraint in (
        ("market_lookup",            "uq_market_lookup_shop_domain_product_url"),
        ("price_intelligence",       "uq_price_intelligence_shop_domain_product_url"),
        ("product_opportunities",    "uq_product_opportunities_shop_domain_product_url"),
        ("unique_product_detection", "uq_unique_product_detection_shop_domain_product_url"),
    ):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )

    # 7. Drop redundant uq_merchants_shop_domain (covered by ix_merchants_shop_domain unique index).
    op.execute(
        "ALTER TABLE merchants DROP CONSTRAINT IF EXISTS uq_merchants_shop_domain"
    )

    # 8. Drop redundant ix_store_metrics_shop (covered by store_metrics_shop_domain_key).
    op.execute("DROP INDEX IF EXISTS ix_store_metrics_shop")

    # 9. Create missing ix_vps_state_shop_visitor on visitor_product_state.
    # Note: name disambiguated from visitor_purchase_sessions which already
    # has ix_vps_shop_visitor (index names are global per schema in Postgres).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vps_state_shop_visitor "
        "ON visitor_product_state (shop_domain, visitor_id)"
    )


def downgrade() -> None:
    # Reverse 9.
    op.execute("DROP INDEX IF EXISTS ix_vps_state_shop_visitor")

    # Reverse 8.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_store_metrics_shop "
        "ON store_metrics (shop_domain)"
    )

    # Reverse 7.
    op.execute(
        "ALTER TABLE merchants ADD CONSTRAINT uq_merchants_shop_domain "
        "UNIQUE (shop_domain)"
    )

    # Reverse 3-6 — recreate redundant duplicates.
    for table, constraint, cols in (
        ("market_lookup",            "uq_market_lookup_shop_domain_product_url",            "shop_domain, product_url"),
        ("price_intelligence",       "uq_price_intelligence_shop_domain_product_url",       "shop_domain, product_url"),
        ("product_opportunities",    "uq_product_opportunities_shop_domain_product_url",    "shop_domain, product_url"),
        ("unique_product_detection", "uq_unique_product_detection_shop_domain_product_url", "shop_domain, product_url"),
    ):
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} UNIQUE ({cols})"
        )

    # Reverse 1-2.
    op.execute(
        "ALTER TABLE evolution_proposals "
        "ALTER COLUMN infra_cost_estimate TYPE VARCHAR(16)"
    )
    op.execute(
        "ALTER TABLE evolution_proposals "
        "ALTER COLUMN impact_radius TYPE VARCHAR(16)"
    )
