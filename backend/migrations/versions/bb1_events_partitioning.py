"""events table — convert to monthly range partition on timestamp

Revision ID: bb1_events_partitioning
Revises: aa1_gdpr_requests
Create Date: 2026-03-24

Strategy (create-copy-swap):
  1. Create events_partitioned with same schema, PARTITION BY RANGE (timestamp)
  2. Create monthly partitions covering existing data + 3 future months
  3. Copy data from events → events_partitioned
  4. Rename events → events_legacy, events_partitioned → events
  5. Recreate indexes on the new partitioned table

The events_legacy table is kept as an instant rollback path.
Drop it manually after verifying production is healthy:
  DROP TABLE IF EXISTS events_legacy;

IMPORTANT — RUN DURING LOW TRAFFIC:
  The INSERT...SELECT step locks the source table briefly.
  For tables under 10M rows this completes in seconds.
  For larger tables, consider batching or pg_partman.

Partition key: timestamp (BigInteger, epoch milliseconds).
Monthly granularity: 1 partition per calendar month.
Retention: DROP PARTITION replaces DELETE WHERE for 90-day cleanup.
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
import calendar

revision = "bb1_events_partitioning"
down_revision = "aa1_gdpr_requests"
branch_labels = None
depends_on = None


def _month_epoch_ms(year: int, month: int) -> int:
    """Return epoch milliseconds for the 1st of the given month (UTC)."""
    dt = datetime(year, month, 1, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _partition_name(year: int, month: int) -> str:
    return f"events_y{year}m{month:02d}"


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create partitioned table with same columns ─────────────────────
    conn.execute(sa.text("""
        CREATE TABLE events_partitioned (
            id          SERIAL,
            visitor_id  VARCHAR,
            event_type  VARCHAR,
            url         VARCHAR,
            product_url VARCHAR,
            timestamp   BIGINT,
            dwell_seconds    INTEGER,
            max_scroll_depth INTEGER,
            shop_domain VARCHAR NOT NULL,
            source_type VARCHAR,
            referrer    VARCHAR,
            product_id  VARCHAR(64),
            PRIMARY KEY (id, timestamp)
        ) PARTITION BY RANGE (timestamp)
    """))

    # ── 2. Determine partition range ──────────────────────────────────────
    # Find the earliest event timestamp for the start bound
    row = conn.execute(sa.text(
        "SELECT MIN(timestamp), MAX(timestamp) FROM events"
    )).fetchone()

    min_ts = row[0] if row and row[0] else None
    max_ts = row[1] if row and row[1] else None

    now = datetime.now(timezone.utc)

    if min_ts is not None:
        start_dt = datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc)
        start_year, start_month = start_dt.year, start_dt.month
    else:
        start_year, start_month = now.year, now.month

    # Create partitions up to 3 months in the future
    end_year, end_month = now.year, now.month
    # Advance 3 months
    for _ in range(3):
        end_month += 1
        if end_month > 12:
            end_month = 1
            end_year += 1

    # Create each monthly partition
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        pname = _partition_name(y, m)
        lo = _month_epoch_ms(y, m)
        # Next month boundary
        nm = m + 1
        ny = y
        if nm > 12:
            nm = 1
            ny += 1
        hi = _month_epoch_ms(ny, nm)

        conn.execute(sa.text(f"""
            CREATE TABLE {pname} PARTITION OF events_partitioned
            FOR VALUES FROM ({lo}) TO ({hi})
        """))

        # Advance
        m += 1
        if m > 12:
            m = 1
            y += 1

    # ── 3. Default partition for any rows outside defined ranges ───────────
    conn.execute(sa.text("""
        CREATE TABLE events_default PARTITION OF events_partitioned DEFAULT
    """))

    # ── 4. Copy data ──────────────────────────────────────────────────────
    # Only copy if the source table has rows.  The COALESCE ensures rows
    # with NULL timestamp land in the default partition.
    if min_ts is not None:
        conn.execute(sa.text("""
            INSERT INTO events_partitioned
                (id, visitor_id, event_type, url, product_url, timestamp,
                 dwell_seconds, max_scroll_depth, shop_domain, source_type,
                 referrer, product_id)
            SELECT
                id, visitor_id, event_type, url, product_url,
                COALESCE(timestamp, 0),
                dwell_seconds, max_scroll_depth, shop_domain, source_type,
                referrer, product_id
            FROM events
        """))

    # ── 5. Swap tables ────────────────────────────────────────────────────
    # Rename old sequence first to avoid naming conflict
    conn.execute(sa.text(
        "ALTER SEQUENCE IF EXISTS events_id_seq RENAME TO events_legacy_id_seq"
    ))
    conn.execute(sa.text("ALTER TABLE events RENAME TO events_legacy"))
    conn.execute(sa.text("ALTER TABLE events_partitioned RENAME TO events"))

    # Reset the new sequence so new inserts get correct IDs
    conn.execute(sa.text("""
        SELECT setval('events_partitioned_id_seq',
                       COALESCE((SELECT MAX(id) FROM events), 0) + 1, false)
    """))
    # Rename the new sequence to match the new table name
    conn.execute(sa.text(
        "ALTER SEQUENCE events_partitioned_id_seq RENAME TO events_id_seq"
    ))

    # ── 6. Recreate indexes ───────────────────────────────────────────────
    conn.execute(sa.text("""
        CREATE INDEX ix_events_shop_ts
        ON events (shop_domain, timestamp DESC)
    """))
    conn.execute(sa.text("""
        CREATE INDEX ix_events_shop_visitor
        ON events (shop_domain, visitor_id)
    """))
    conn.execute(sa.text("""
        CREATE INDEX ix_events_shop_product
        ON events (shop_domain, product_url)
        WHERE product_url IS NOT NULL
    """))

    # ── 7. Helper function for future partition creation ──────────────────
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION create_events_partition(
            p_year INT, p_month INT
        ) RETURNS VOID AS $$
        DECLARE
            pname TEXT;
            lo BIGINT;
            hi BIGINT;
            nm INT;
            ny INT;
        BEGIN
            pname := 'events_y' || p_year || 'm' || LPAD(p_month::TEXT, 2, '0');
            -- Epoch ms for first of month
            lo := EXTRACT(EPOCH FROM make_timestamp(p_year, p_month, 1, 0, 0, 0)) * 1000;
            nm := p_month + 1;
            ny := p_year;
            IF nm > 12 THEN nm := 1; ny := ny + 1; END IF;
            hi := EXTRACT(EPOCH FROM make_timestamp(ny, nm, 1, 0, 0, 0)) * 1000;

            -- Idempotent: skip if partition exists
            IF NOT EXISTS (
                SELECT 1 FROM pg_class WHERE relname = pname
            ) THEN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%s) TO (%s)',
                    pname, lo, hi
                );
                RAISE NOTICE 'Created partition %', pname;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    # Restore sequence names
    conn.execute(sa.text(
        "ALTER SEQUENCE IF EXISTS events_id_seq RENAME TO events_partitioned_id_seq"
    ))
    # Swap tables back
    conn.execute(sa.text("ALTER TABLE events RENAME TO events_partitioned"))
    conn.execute(sa.text("ALTER TABLE events_legacy RENAME TO events"))
    conn.execute(sa.text(
        "ALTER SEQUENCE IF EXISTS events_legacy_id_seq RENAME TO events_id_seq"
    ))
    # Drop partitioned table and all partitions
    conn.execute(sa.text("DROP TABLE IF EXISTS events_partitioned CASCADE"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS create_events_partition(INT, INT)"))
