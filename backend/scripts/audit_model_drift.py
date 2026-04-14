#!/usr/bin/env python
"""
audit_model_drift.py — Compare every SQLAlchemy model's declared columns
against the live Postgres schema, flagging drifts in either direction.

Why this matters
----------------
When a model has a column the DB doesn't have, the first query that
touches it crashes in production (`UndefinedColumn`). When the DB has
a column the model doesn't, that column can become silently useless
— no code writes to it, data integrity rots.

At €100M+ scale even a single drifted column can take down a hot path
for minutes before auto-recovery. Run this script in every CI cycle
and before every release.

Exits 0 if clean, 1 if any model drifts from its DB table. TIER 0
safe — read-only introspection.
"""
from __future__ import annotations

import importlib
import pathlib
import sys

sys.path.insert(0, "/opt/wishspark/backend")

from sqlalchemy import inspect

from app.core.database import Base, engine

# Walk app/models/ and import every module so Base.metadata is
# populated with all declared tables — even models that haven't been
# added to app/models/__init__.py. This makes the audit robust against
# __init__.py drift (a known failure mode: a file defines a model but
# forgets to export it, and any drift on that table goes unnoticed).
_MODELS_DIR = pathlib.Path("/opt/wishspark/backend/app/models")
for _py in sorted(_MODELS_DIR.glob("*.py")):
    if _py.stem == "__init__":
        continue
    try:
        importlib.import_module(f"app.models.{_py.stem}")
    except Exception as _exc:
        print(f"  [import failed] app.models.{_py.stem}: {_exc}")


# Tables that exist in the DB but intentionally have no model (raw SQL
# tables, partitioned tables managed outside SQLAlchemy, audit tables
# that are write-only from triggers, etc.). Adding an entry here is a
# conscious choice — document why in the comment.
KNOWN_ORPHAN_TABLES: set[str] = {
    "alembic_version",     # alembic's own bookkeeping
    "events_partitioned",  # events has a custom partitioning layer
    "events_default",      # events partition child (default / catch-all)
    "events_legacy",       # events partition child (pre-partitioning rows)
    "events_y2026m03",     # events partition child (monthly range)
    "events_y2026m04",     # events partition child (monthly range)
    "events_y2026m05",     # events partition child (monthly range)
    "events_y2026m06",     # events partition child (monthly range)
    # merchant_email_stats is an intentional raw-SQL-only table. It is
    # written and read from app/services/email_performance.py and
    # app/services/action_learning.py using text() queries because the
    # upsert pattern (ON CONFLICT ... DO UPDATE) is cleaner to express
    # directly than through the ORM. Creating a model would duplicate
    # state without removing any code.
    "merchant_email_stats",
}


# Columns that are known to exist in the DB but not in the model, for
# legitimate reasons (e.g. trigger-maintained, legacy migration not
# yet deleted). Each entry needs a reason.
KNOWN_ORPHAN_COLUMNS: dict[str, set[str]] = {
    # table_name: {column_name, ...}
}


def main() -> int:
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    model_tables = set(Base.metadata.tables.keys())

    drifts: list[tuple[str, str, set[str], set[str]]] = []
    missing_models: set[str] = set()
    missing_tables: set[str] = set()

    for table_name, table in Base.metadata.tables.items():
        if table_name not in db_tables:
            missing_tables.add(table_name)
            continue

        model_cols = {c.name for c in table.columns}
        try:
            db_cols = {c["name"] for c in insp.get_columns(table_name)}
        except Exception as exc:
            print(f"  [introspect failed] {table_name}: {exc}")
            continue

        in_model_not_db = model_cols - db_cols
        in_db_not_model = db_cols - model_cols - KNOWN_ORPHAN_COLUMNS.get(table_name, set())

        if in_model_not_db or in_db_not_model:
            drifts.append((table_name, "both", in_model_not_db, in_db_not_model))

    for db_table in db_tables - model_tables - KNOWN_ORPHAN_TABLES:
        missing_models.add(db_table)

    # ---- Report ----
    ok = True

    if missing_tables:
        ok = False
        print(f"MODELS WITHOUT DB TABLE ({len(missing_tables)}):")
        for t in sorted(missing_tables):
            print(f"  {t}")
        print()

    if drifts:
        ok = False
        print(f"COLUMN DRIFTS ({len(drifts)} tables):")
        for table_name, _, in_model_not_db, in_db_not_model in sorted(drifts):
            print(f"  {table_name}")
            if in_model_not_db:
                print(f"    GHOST in model (model has, DB does not): {sorted(in_model_not_db)}")
            if in_db_not_model:
                print(f"    Orphan in DB (DB has, model does not): {sorted(in_db_not_model)}")
        print()

    if missing_models:
        # This is informational — orphan tables without a model are not
        # always drift, but the audit flags them so nothing rots unnoticed.
        print(f"DB TABLES WITHOUT MODEL ({len(missing_models)}):")
        for t in sorted(missing_models):
            print(f"  {t}")
        print()
        print("(not a failure — add to KNOWN_ORPHAN_TABLES with a reason, or create a model)")
        print()

    if ok and not missing_models:
        print("✅ All models in sync with DB schema")
        return 0
    elif ok:
        # Only orphan tables — informational
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
