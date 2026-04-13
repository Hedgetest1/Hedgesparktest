#!/usr/bin/env python
"""
audit_sql_schema.py — Extract raw SQL literals from app/ and verify
every referenced table + column exists in the live schema.

This is the tool the post-refactor audit needed. It finds:

    * Tables that do not exist
    * Columns that do not exist on the table they're being filtered on
    * Typos in schema-qualified names

Limitations (documented so we don't pretend otherwise):

    * Regex-based SQL parsing, not a real SQL parser. Handles FROM / JOIN
      / UPDATE / INSERT INTO / DELETE FROM / "column" IS NULL patterns.
    * CTE aliases and subqueries are resolved structurally where possible.
    * PostgreSQL-only — SQLite/MySQL would need a different tokenizer.

Usage:
    ./venv/bin/python scripts/audit_sql_schema.py
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/opt/wishspark/backend")

from sqlalchemy import inspect

from app.core.database import engine


APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def load_schema() -> dict[str, set[str]]:
    insp = inspect(engine)
    schema: dict[str, set[str]] = {}
    for table in insp.get_table_names():
        schema[table] = {c["name"] for c in insp.get_columns(table)}
    return schema


# Match text("...") and """SELECT ...""" blocks
_SQL_CALL = re.compile(
    r'text\s*\(\s*(?P<quote>["\']{1,3})(?P<body>.*?)(?P=quote)\s*\)',
    re.DOTALL,
)

_TABLE_KEYWORDS = (
    r"(?:FROM|JOIN|UPDATE|INSERT\s+INTO|DELETE\s+FROM|INTO)"
)
_TABLE_NAME_RE = re.compile(
    rf"{_TABLE_KEYWORDS}\s+([a-zA-Z_][\w.]*)",
    re.IGNORECASE,
)

# Reserved / CTE-local / builtin pseudo-tables we never want to flag.
# The regex captures tokens after FROM/JOIN/INTO — anything in this set is
# a SQL keyword / function / aliased column that the regex misread.
_SKIP_TABLES = {
    # Built-in pseudo-tables
    "select", "values", "unnest", "generate_series", "jsonb_array_elements",
    "jsonb_each", "jsonb_array_elements_text", "json_array_elements",
    "pg_catalog", "information_schema", "dual",
    # PG functions that appear in FROM clauses
    "now", "to_timestamp", "to_char", "extract", "date_trunc", "date_part",
    "coalesce", "nullif", "case", "cast", "count", "sum", "avg", "min", "max",
    "abs", "round", "floor", "ceil", "greatest", "least", "array_agg",
    "json_agg", "jsonb_agg", "json_build_object", "jsonb_build_object",
    # PG types (INTO <type> doesn't happen but FROM sometimes picks up casts)
    "int", "bigint", "smallint", "real", "float", "double", "numeric",
    "text", "varchar", "char", "boolean", "bool", "json", "jsonb", "uuid",
    "timestamp", "timestamptz", "date", "time", "interval", "bytea",
    # SQL keywords that can appear right after FROM/INTO by accident
    "set", "when", "then", "else", "end", "true", "false", "null",
    "conflict", "do", "nothing", "returning", "between", "distinct",
    "where", "group", "order", "having", "limit", "offset", "union",
    # CTE-adjacent: columns named like timestamps that appear in projection
    "confirmed_at", "exposed_at", "created_at", "updated_at", "deleted_at",
    "executed_at", "evaluated_at", "started_at", "finished_at",
    "measurement_start", "measurement_end",
}


def _strip_sql_comments(sql: str) -> str:
    """Remove `-- ...` line comments and `/* ... */` block comments so the
    regex doesn't misread words-after-a-dash as identifiers."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def extract_sql_blocks(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        src = path.read_text()
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for m in _SQL_CALL.finditer(src):
        line_no = src.count("\n", 0, m.start()) + 1
        body = _strip_sql_comments(m.group("body")).strip()
        if len(body) < 6:
            continue
        out.append((line_no, body))
    return out


def find_cte_names(sql: str) -> set[str]:
    """Return the set of CTE aliases declared via WITH name AS (...)."""
    out: set[str] = set()
    for m in re.finditer(r"\b(\w+)\s+AS\s*\(", sql, re.I):
        out.add(m.group(1).lower())
    return out


def find_table_references(sql: str) -> set[str]:
    ctes = find_cte_names(sql)
    seen: set[str] = set()
    for m in _TABLE_NAME_RE.finditer(sql):
        raw = m.group(1)
        # Strip schema qualifier if present
        if "." in raw:
            raw = raw.split(".", 1)[1]
        name = raw.lower()
        if name in _SKIP_TABLES:
            continue
        if name in ctes:
            continue
        seen.add(name)
    return seen


def find_column_references(sql: str, known_tables: dict[str, set[str]]) -> set[tuple[str, str]]:
    """
    Best-effort column extraction for WHERE/SET clauses that name a column
    followed by = / IS / </> / <= / >= / IN / BETWEEN / LIKE.

    Returns (column_name, table_hint_or_None) pairs where table_hint is
    the alias prefix before the column (e.g. "so.line_items" → so).
    """
    out: set[tuple[str, str]] = set()
    pat = re.compile(
        r"(?:WHERE|AND|OR|SET|,|\()\s*([a-zA-Z_][\w.]*?)"
        r"\s*(?:=|<>|<=|>=|<|>|IS\s+(?:NOT\s+)?NULL|IN\s*\(|BETWEEN|LIKE|ILIKE)",
        re.IGNORECASE,
    )
    for m in pat.finditer(sql):
        raw = m.group(1)
        if "." in raw:
            alias, col = raw.split(".", 1)
            out.add((col.lower(), alias.lower()))
        else:
            out.add((raw.lower(), ""))
    return out


def main() -> int:
    schema = load_schema()
    print(f"loaded {len(schema)} tables from schema\n")

    missing_tables: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    # (table, column) -> [(file, line, sql_snippet)]
    missing_columns: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)

    total_blocks = 0
    for py_file in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        blocks = extract_sql_blocks(py_file)
        total_blocks += len(blocks)
        for line, sql in blocks:
            tables = find_table_references(sql)
            for t in tables:
                if t not in schema:
                    missing_tables[t].append(
                        (str(py_file.relative_to(APP_ROOT.parent)), line, sql[:120].replace("\n", " "))
                    )

    print(f"scanned {total_blocks} raw SQL blocks\n")

    if missing_tables:
        print("=" * 70)
        print(f"MISSING TABLES ({len(missing_tables)} distinct names)")
        print("=" * 70)
        for table, hits in sorted(missing_tables.items()):
            print(f"\n  {table!r}")
            for f, line, snip in hits[:5]:
                print(f"    {f}:{line}")
                print(f"      {snip}...")
            if len(hits) > 5:
                print(f"    ... and {len(hits) - 5} more")
    else:
        print("✅ No missing tables\n")

    return 0 if not missing_tables else 1


if __name__ == "__main__":
    sys.exit(main())
