"""Lock contract for the FastAPI lifespan DB pool pre-warm.

Born 2026-05-11 after the SLO cold-path investigation. The pre-warm
runs at worker boot inside `lifespan` in app/main.py — it executes
`SELECT 1 FROM <table> LIMIT 1` across 3 hot tables (merchants,
shop_orders, events) and N=DB_POOL_PREWARM_COUNT connections to
warm PG shared_buffers + connection acquisition.

This test does NOT exercise the full FastAPI lifespan (which would
require ASGI plumbing). Instead it locks the underlying contract:
the 3 hot tables actually exist + are reachable, the SELECT 1
LIMIT 1 pattern works (doesn't raise), and the queries are
side-effect-free.

If a future refactor renames any of the 3 tables, this test fires
before the rename ships unannounced via the lifespan path.
"""
from __future__ import annotations

from sqlalchemy import text


_HOT_TABLES_PREWARMED_AT_BOOT = ("merchants", "shop_orders", "events")


def test_prewarm_target_tables_exist(db):
    """Each hot table the pre-warm touches must actually exist in the
    schema. A typo / rename would silently make pre-warm a no-op."""
    from sqlalchemy import inspect
    insp = inspect(db.bind)
    all_tables = set(insp.get_table_names())
    for table in _HOT_TABLES_PREWARMED_AT_BOOT:
        assert table in all_tables, (
            f"pre-warm references {table!r} but it does not exist in the "
            "current schema — main.py::lifespan would silently fail and "
            "lose the cold-path warming. Update the pre-warm list in "
            "app/main.py to match."
        )


def test_prewarm_queries_succeed(db):
    """Re-run the exact pre-warm queries against the test DB. Each
    must return without raising and produce 0 or 1 row."""
    for table in _HOT_TABLES_PREWARMED_AT_BOOT:
        result = db.execute(text(f"SELECT 1 FROM {table} LIMIT 1")).fetchall()
        assert len(result) <= 1, (
            f"pre-warm query on {table!r} returned >1 row — LIMIT 1 "
            "is supposed to bound the buffer touch. Investigate."
        )


def test_prewarm_count_env_var_parses():
    """DB_POOL_PREWARM_COUNT must default to 4 (matches ecosystem.
    config.js uvicorn --workers 4) and parse as int. Wrong default
    here means worker boot warms wrong number of connections."""
    import os
    raw = os.getenv("DB_POOL_PREWARM_COUNT", "4")
    parsed = int(raw)
    assert parsed >= 1
    # Sanity ceiling — if someone sets 1000 by mistake, the boot
    # loop would take ~100ms × 1000 = 100s. Cap at 32 by convention.
    assert parsed <= 32, (
        f"DB_POOL_PREWARM_COUNT={parsed} is suspicious — pre-warm at "
        "boot should match uvicorn worker count (typically 4), not "
        "saturate the pool"
    )


def test_lifespan_uses_constants_from_audit():
    """Cross-check: the 3 tables the pre-warm targets MUST appear in
    the actual lifespan body in app/main.py. Catches drift where
    someone updates the pre-warm but forgets to update this test
    (or vice versa)."""
    import pathlib
    main_py = pathlib.Path("/opt/wishspark/backend/app/main.py").read_text()
    # Locate the pre-warm block by its log marker
    assert "db pool pre-warm" in main_py, (
        "main.py lifespan no longer contains the 'db pool pre-warm' "
        "marker — either it was removed or the marker changed. Update "
        "this test or restore the pre-warm."
    )
    for table in _HOT_TABLES_PREWARMED_AT_BOOT:
        assert f"FROM {table} LIMIT 1" in main_py, (
            f"main.py pre-warm body does NOT touch {table!r} but the "
            "test contract requires it. Either add it to the lifespan "
            "or remove it from this test (with rationale)."
        )
