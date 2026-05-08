"""pro perf composite indexes — close 8 endpoint p95 drift findings 2026-05-08

Post-supersession brutal perf-hunt (Agent investigation found 5 root cause
patterns across 8 Pro endpoints showing 1.5-1.9× p95 drift):

  Pattern A (events visitor_id covering): /pro/cohorts/behavioral, /pro/instant-intelligence
  Pattern C (shop_orders currency filter): /orders/forecast/pro, /analytics/top-variants
  product_metrics views_7d filter: /pro/price-sensitivity

Three composite indexes added with CREATE INDEX CONCURRENTLY (non-blocking
at production load — 200+ Pro merchants reading these tables every cycle).

Revision ID: aa1_pro_perf_composite_indexes
Revises: 358d6df7
"""
from alembic import op

revision = "aa1_pro_perf_composite_indexes"
down_revision = "358d6df7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction.
    with op.get_context().autocommit_block():
        # 1. shop_orders (shop_domain, currency, created_at):
        # /orders/forecast/pro, /analytics/top-variants and revenue window
        # queries filter by all 3 columns. Existing index is
        # (shop_domain, created_at) → forces filter thrashing on multi-
        # currency shops. New index satisfies WHERE + ORDER BY from index.
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_shop_orders_shop_currency_created
            ON shop_orders (shop_domain, currency, created_at DESC)
        """)

        # 2. events (shop_domain, visitor_id, timestamp DESC):
        # /pro/cohorts/behavioral, /pro/instant-intelligence run
        # `WHERE shop_domain=:s AND visitor_id = ANY(:vids) GROUP BY visitor_id
        #  ORDER BY MIN(timestamp)`. Existing ix_events_shop_visitor lacks
        # timestamp covering → forces table scan + sort for the aggregation.
        # The new index covers the GROUP BY + MIN(timestamp) directly.
        #
        # `events` is RANGE-partitioned on `timestamp`. PG doesn't support
        # CREATE INDEX CONCURRENTLY on a partitioned parent. Standard pattern:
        # build CONCURRENTLY on each child partition, then attach a partitioned
        # parent index ON ONLY the parent (non-concurrent but instant since
        # all children already have matching indexes attached).
        events_partitions = [
            "events_y2026m03",
            "events_y2026m04",
            "events_y2026m05",
            "events_y2026m06",
            "events_default",
        ]
        for child in events_partitions:
            op.execute(f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS
                    ix_{child}_shop_visitor_ts
                ON {child} (shop_domain, visitor_id, "timestamp" DESC)
            """)
        # Parent-level partitioned index: ONLY creates the catalog entry,
        # then ATTACH PARTITION binds each child's index. Both are
        # near-instant operations (no data scan).
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_events_shop_visitor_ts
            ON ONLY events (shop_domain, visitor_id, "timestamp" DESC)
        """)
        for child in events_partitions:
            op.execute(f"""
                ALTER INDEX ix_events_shop_visitor_ts
                ATTACH PARTITION ix_{child}_shop_visitor_ts
            """)

        # 3. product_metrics (shop_domain, views_7d):
        # /pro/price-sensitivity executes
        # `WHERE shop_domain=:s AND views_7d >= 3`. Existing index
        # (shop_domain, unique_visitors_24h DESC) doesn't help. Fetches
        # all rows then filters in Python (200+ products per shop).
        # New index pushes the predicate to the planner.
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                ix_product_metrics_shop_views_7d
            ON product_metrics (shop_domain, views_7d)
            WHERE views_7d >= 3
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_product_metrics_shop_views_7d")
        # Partitioned index: drop parent first cascades to children
        op.execute("DROP INDEX IF EXISTS ix_events_shop_visitor_ts")
        for child in ["events_y2026m03", "events_y2026m04", "events_y2026m05",
                      "events_y2026m06", "events_default"]:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS ix_{child}_shop_visitor_ts")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_shop_orders_shop_currency_created")
