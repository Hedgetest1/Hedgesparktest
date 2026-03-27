#!/usr/bin/env python3
import json
import os
import sys
import time
from contextlib import suppress
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def ok(msg: str) -> None:
    print(f"PASS  {msg}")


def warn(msg: str) -> None:
    print(f"WARN  {msg}")


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")


def info(msg: str) -> None:
    print(f"INFO  {msg}")


def load_env() -> None:
    load_dotenv("/opt/wishspark/backend/.env")
    load_dotenv()


def get_engine():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL non trovato")
    return create_engine(db_url, future=True, pool_pre_ping=True)


def fetch_one(conn, sql: str, params: Dict[str, Any] | None = None):
    return conn.execute(text(sql), params or {}).fetchone()


def fetch_all(conn, sql: str, params: Dict[str, Any] | None = None):
    return conn.execute(text(sql), params or {}).fetchall()


def exists_table(conn, table_name: str) -> bool:
    row = fetch_one(
        conn,
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :table_name
        )
        """,
        {"table_name": table_name},
    )
    return bool(row[0])


def exists_function(conn, function_name: str) -> bool:
    row = fetch_one(
        conn,
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_proc
            WHERE proname = :function_name
        )
        """,
        {"function_name": function_name},
    )
    return bool(row[0])


def check_events_partitioning(conn) -> bool:
    overall = True

    row = fetch_one(
        conn,
        """
        SELECT relkind
        FROM pg_class
        WHERE relname = 'events'
        """
    )
    if not row:
        fail("tabella events non trovata")
        return False

    relkind = row[0]
    if relkind == "p":
        ok("events è una tabella partitioned (relkind = 'p')")
    else:
        fail(f"events NON è partitioned (relkind = '{relkind}')")
        overall = False

    parts = fetch_all(
        conn,
        """
        SELECT inhrelid::regclass::text AS partition_name
        FROM pg_inherits
        WHERE inhparent = 'events'::regclass
        ORDER BY 1
        """
    )
    if parts:
        ok(f"partizioni events trovate: {len(parts)}")
        for p in parts:
            info(f"partition: {p[0]}")
    else:
        fail("nessuna partizione trovata per events")
        overall = False

    if exists_table(conn, "events_legacy"):
        row_events = fetch_one(conn, "SELECT COUNT(*) FROM events")
        row_legacy = fetch_one(conn, "SELECT COUNT(*) FROM events_legacy")
        if row_events and row_legacy:
            if row_events[0] == row_legacy[0]:
                ok(f"row count events == events_legacy ({row_events[0]})")
            else:
                fail(
                    f"row count mismatch: events={row_events[0]} vs events_legacy={row_legacy[0]}"
                )
                overall = False
    else:
        warn("events_legacy non esiste: niente confronto row count rollback-safe")

    idx = fetch_all(
        conn,
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'events'
        ORDER BY 1
        """
    )
    idx_names = {r[0] for r in idx}
    expected_any = {
        "ix_events_shop_ts",
        "ix_events_shop_visitor",
        "ix_events_shop_product",
    }
    found = expected_any.intersection(idx_names)
    if found:
        ok(f"indici parent events trovati: {sorted(found)}")
    else:
        warn(
            "nessuno degli indici attesi trovato sul parent events; verificare naming/partition indexes"
        )

    if exists_function(conn, "create_events_partition"):
        ok("funzione create_events_partition presente")
    else:
        fail("funzione create_events_partition assente")
        overall = False

    return overall


def check_events_insert_delete(conn) -> bool:
    overall = True
    visitor_id = f"verify-{int(time.time())}"
    shop_domain = "test.myshopify.com"
    ts_ms = int(time.time() * 1000)

    try:
        conn.execute(
            text(
                """
                INSERT INTO events (visitor_id, event_type, url, timestamp, shop_domain)
                VALUES (:visitor_id, 'test', '/test', :ts, :shop_domain)
                """
            ),
            {"visitor_id": visitor_id, "ts": ts_ms, "shop_domain": shop_domain},
        )
        conn.commit()

        row = fetch_one(
            conn,
            """
            SELECT id, timestamp
            FROM events
            WHERE visitor_id = :visitor_id
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"visitor_id": visitor_id},
        )
        if row:
            ok(f"insert events riuscito (id={row[0]}, timestamp={row[1]})")
        else:
            fail("insert events eseguito ma riga non trovata")
            overall = False

        explain_rows = fetch_all(
            conn,
            """
            EXPLAIN
            SELECT *
            FROM events
            WHERE shop_domain = :shop_domain
              AND timestamp > :ts
            """,
            {"shop_domain": shop_domain, "ts": ts_ms - 1000},
        )
        plan = "\n".join(r[0] for r in explain_rows)
        print("INFO  EXPLAIN plan:")
        print(plan)

        conn.execute(
            text("DELETE FROM events WHERE visitor_id = :visitor_id"),
            {"visitor_id": visitor_id},
        )
        conn.commit()

        deleted = fetch_one(
            conn,
            "SELECT COUNT(*) FROM events WHERE visitor_id = :visitor_id",
            {"visitor_id": visitor_id},
        )
        if deleted and deleted[0] == 0:
            ok("cleanup evento di test riuscito")
        else:
            fail("cleanup evento di test NON riuscito")
            overall = False

    except SQLAlchemyError as e:
        conn.rollback()
        fail(f"test insert/delete events fallito: {e}")
        overall = False

    return overall


def check_tables(conn) -> bool:
    overall = True

    expected_tables = [
        "gdpr_requests",
        "merchants",
        "events",
    ]
    for table_name in expected_tables:
        if exists_table(conn, table_name):
            ok(f"tabella presente: {table_name}")
        else:
            fail(f"tabella assente: {table_name}")
            overall = False

    # nudge_impression_daily era un caveat noto
    if exists_table(conn, "nudge_impression_daily"):
        ok("tabella presente: nudge_impression_daily")
    else:
        warn("tabella assente: nudge_impression_daily (caveat già noto)")

    return overall


def check_worker_state_tables(conn) -> bool:
    overall = True

    for table_name in ["worker_state", "worker_log"]:
        if exists_table(conn, table_name):
            ok(f"tabella worker presente: {table_name}")
        else:
            warn(f"tabella worker assente: {table_name}")
            overall = False

    if exists_table(conn, "worker_state"):
        rows = fetch_all(
            conn,
            """
            SELECT worker_name, last_success_at, last_run_at
            FROM worker_state
            ORDER BY worker_name
            """
        )
        if rows:
            ok(f"worker_state contiene {len(rows)} righe")
            for r in rows:
                info(f"worker={r[0]} last_success_at={r[1]} last_run_at={r[2]}")
        else:
            warn("worker_state vuota")

    return overall


def check_gdpr_queue(conn) -> bool:
    overall = True

    if not exists_table(conn, "gdpr_requests"):
        fail("gdpr_requests assente")
        return False

    cols = fetch_all(
        conn,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'gdpr_requests'
        ORDER BY ordinal_position
        """
    )
    col_names = [c[0] for c in cols]
    required_cols = {
        "request_type",
        "shop_domain",
        "status",
        "payload",
        "created_at",
    }
    missing = required_cols - set(col_names)
    if missing:
        fail(f"gdpr_requests senza colonne richieste: {sorted(missing)}")
        overall = False
    else:
        ok("gdpr_requests ha le colonne richieste principali")

    row = fetch_one(
        conn,
        "SELECT COUNT(*) FROM gdpr_requests"
    )
    if row is not None:
        ok(f"gdpr_requests accessibile (rows={row[0]})")

    return overall


def check_runtime_endpoints() -> bool:
    overall = True

    urls = [
        ("health", "http://127.0.0.1:8000/health"),
        ("system_health", "http://127.0.0.1:8000/system/health"),
    ]

    for label, url in urls:
        try:
            r = requests.get(url, timeout=5)
            info(f"{label} status_code={r.status_code}")
            if r.status_code in (200, 503):
                ok(f"endpoint {label} risponde")
                with suppress(Exception):
                    payload = r.json()
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                fail(f"endpoint {label} risponde con status inatteso {r.status_code}")
                overall = False
        except Exception as e:
            fail(f"endpoint {label} non raggiungibile: {e}")
            overall = False

    return overall


def check_tracker_files() -> bool:
    overall = True

    canonical = "/opt/wishspark/tracker/spark-tracker.js"
    legacy_1 = "/opt/wishspark/tracker.js"
    legacy_2 = "/opt/wishspark/dashboard/public/tracker.js"

    if os.path.exists(canonical):
        ok(f"tracker canonico presente: {canonical}")
    else:
        fail(f"tracker canonico assente: {canonical}")
        overall = False

    if not os.path.exists(legacy_1):
        ok("legacy tracker root assente")
    else:
        fail(f"legacy tracker ancora presente: {legacy_1}")
        overall = False

    if not os.path.exists(legacy_2):
        ok("legacy tracker dashboard/public assente")
    else:
        fail(f"legacy tracker ancora presente: {legacy_2}")
        overall = False

    return overall


def check_pm2_processes() -> bool:
    overall = True
    try:
        import subprocess

        proc = subprocess.run(
            ["pm2", "jlist"],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(proc.stdout)
        names = {item.get("name") for item in data}

        expected = {
            "wishspark-backend",
            "wishspark-dashboard",
            "wishspark-worker",
            "wishspark-agent-worker",
            "wishspark-aggregation-worker",
            "wishspark-segment-monitor",
            "wishspark-nudge-optimizer",
            "wishspark-gdpr-worker",
        }

        missing = expected - names
        if missing:
            fail(f"processi PM2 mancanti: {sorted(missing)}")
            overall = False
        else:
            ok("tutti i processi PM2 attesi presenti")

        for item in data:
            name = item.get("name")
            status = item.get("pm2_env", {}).get("status")
            if name in expected:
                if status == "online":
                    ok(f"PM2 online: {name}")
                else:
                    warn(f"PM2 non online: {name} ({status})")

    except Exception as e:
        fail(f"impossibile verificare PM2: {e}")
        overall = False

    return overall


def check_openai_cache_code() -> bool:
    overall = True
    target = "/opt/wishspark/backend/app/services/nudge_composer.py"
    if not os.path.exists(target):
        fail(f"file assente: {target}")
        return False

    content = open(target, "r", encoding="utf-8").read()

    checks = [
        ("cache key SHA-256", "sha256" in content.lower()),
        ("cache read", "cache_get" in content or "redis" in content.lower()),
        ("cache write", "cache_set" in content or "setex" in content.lower()),
    ]

    for label, passed in checks:
        if passed:
            ok(f"nudge_composer: {label}")
        else:
            warn(f"nudge_composer: {label} non trovato chiaramente")
            overall = False

    return overall


def main() -> int:
    load_env()

    overall_results: List[Tuple[str, bool]] = []

    info("=== VERIFY HARDENING START ===")

    overall_results.append(("tracker_files", check_tracker_files()))
    overall_results.append(("pm2_processes", check_pm2_processes()))
    overall_results.append(("openai_cache_code", check_openai_cache_code()))

    try:
        engine = get_engine()
    except Exception as e:
        fail(f"creazione engine fallita: {e}")
        return 2

    try:
        with engine.connect() as conn:
            overall_results.append(("tables", check_tables(conn)))
            overall_results.append(("gdpr_queue", check_gdpr_queue(conn)))
            overall_results.append(("worker_state", check_worker_state_tables(conn)))
            overall_results.append(("events_partitioning", check_events_partitioning(conn)))
            overall_results.append(("events_insert_delete", check_events_insert_delete(conn)))
    except Exception as e:
        fail(f"verifica DB fallita: {e}")
        return 2

    overall_results.append(("runtime_endpoints", check_runtime_endpoints()))

    print("\n=== SUMMARY ===")
    failed = []
    warned = []

    for name, passed in overall_results:
        if passed:
            print(f"PASS  {name}")
        else:
            print(f"FAIL  {name}")
            failed.append(name)

    if failed:
        print("\nFINAL: FAIL")
        print("Checks failed:", ", ".join(failed))
        return 1

    print("\nFINAL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
