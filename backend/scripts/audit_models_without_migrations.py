#!/usr/bin/env python
"""
audit_models_without_migrations.py — preflight invariant.

Detects SQLAlchemy models declared in `app/models/*.py` whose table is
NOT covered by any Alembic migration. This is the bug class Gap #5 hit:
`merchant_groups` had a model + API + frontend wired and shipping, but
no migration. The tables existed only because `Base.metadata.create_all`
ran at app startup — which silently masks missing migrations and breaks
fresh production deploys.

What this audits
----------------
For every SQLAlchemy ORM model under app/models/:
  * Extract `__tablename__`
  * Search every migration file for a CREATE TABLE / op.create_table
    referencing that table name (raw SQL OR Alembic op API).
  * If neither hit AND the table doesn't exist in the live DB → flag.
  * If the table exists in the live DB but no migration → flag (drift,
    typically caused by `create_all` populating it on first boot).

Exit non-zero on any finding so preflight blocks the commit.

Usage
-----
    ./venv/bin/python scripts/audit_models_without_migrations.py
    ./venv/bin/python scripts/audit_models_without_migrations.py --json
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

sys.path.insert(0, "/opt/wishspark/backend")

from sqlalchemy import inspect

from app.core.database import engine
from _audit_io import safe_read_text

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def decorator(fn):
            return fn
        return decorator


REPO_ROOT = pathlib.Path("/opt/wishspark/backend")
MODELS_DIR = REPO_ROOT / "app" / "models"
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"


# Match `__tablename__ = "name"` — single or double quoted.
_TABLENAME_RE = re.compile(
    r"__tablename__\s*=\s*['\"]([A-Za-z0-9_]+)['\"]"
)


def discover_models() -> dict[str, str]:
    """Return {tablename: model_filename} for every model under app/models/."""
    models: dict[str, str] = {}
    for py in sorted(MODELS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = safe_read_text(py, errors="replace")
        if text is None:
            continue
        for match in _TABLENAME_RE.finditer(text):
            models[match.group(1)] = py.name
    return models


def collect_migration_table_names() -> set[str]:
    """Return every table name referenced in any migration file via:
       * op.create_table('name', ...)
       * CREATE TABLE [IF NOT EXISTS] name (
       * CREATE TABLE [IF NOT EXISTS] "name" (
    """
    seen: set[str] = set()
    op_create = re.compile(r"op\.create_table\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]")
    raw_create = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?([A-Za-z0-9_]+)[\"']?\s*\(",
        re.IGNORECASE,
    )
    for py in MIGRATIONS_DIR.glob("*.py"):
        text = safe_read_text(py, errors="replace")
        if text is None:
            continue
        for m in op_create.finditer(text):
            seen.add(m.group(1))
        for m in raw_create.finditer(text):
            seen.add(m.group(1))
    return seen


def db_tables() -> set[str]:
    """Live tables in the DB (used to distinguish 'drift' from 'never-created')."""
    try:
        return set(inspect(engine).get_table_names())
    except Exception:
        return set()


@telemetered("audit_models_without_migrations")
def audit() -> int:
    models = discover_models()
    in_migrations = collect_migration_table_names()
    in_db = db_tables()

    findings = []
    for table, source_file in sorted(models.items()):
        if table in in_migrations:
            continue
        # Not covered by any migration — classify
        if table in in_db:
            findings.append({
                "table": table,
                "model_file": source_file,
                "severity": "drift",
                "hint": (
                    "Table exists in DB but no migration covers it — "
                    "likely created by Base.metadata.create_all at boot. "
                    "Write a migration that uses CREATE TABLE IF NOT EXISTS "
                    "so fresh deploys + tracked schema both work."
                ),
            })
        else:
            findings.append({
                "table": table,
                "model_file": source_file,
                "severity": "missing",
                "hint": (
                    "Model exists with __tablename__ but neither a migration "
                    "nor a live DB table — endpoints querying this table "
                    "will 500 in production. Write the migration."
                ),
            })

    if "--json" in sys.argv:
        print(json.dumps({
            "total_models": len(models),
            "covered_by_migrations": len(models) - len(findings),
            "findings": findings,
        }, indent=2))
    else:
        if not findings:
            print(f"✓ all {len(models)} models have migrations covering their tables")
            return 0
        print(f"✗ {len(findings)} model(s) without migrations:")
        for f in findings:
            print(f"  • {f['table']:<40s} ({f['severity']:<7s}) — {f['model_file']}")
        print()
        print("Hint: write CREATE TABLE IF NOT EXISTS to cover both fresh deploys")
        print("and dev DBs where create_all already produced the table.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
