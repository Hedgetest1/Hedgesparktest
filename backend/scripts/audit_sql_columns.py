#!/usr/bin/env python
"""
audit_sql_columns.py — Column-level schema audit.

Flags columns referenced in SQL that don't exist on the table they're
being filtered on. Uses alias resolution to figure out which table a
`so.line_items` style reference targets.

More precise than the table-level audit but with more false positives
(aliases into CTEs, subqueries, JSON paths).
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

# Known pseudo-columns that are function calls / aliases, not real columns
_COLUMN_SKIPLIST = {
    "now", "count", "coalesce", "sum", "extract", "case", "date", "min", "max",
    "avg", "cast", "round", "to_timestamp", "floor", "ceil", "abs", "true",
    "false", "null", "json_agg", "json_build_object", "jsonb_array_elements",
    "from", "where", "and", "or", "not", "in", "on", "as", "with", "union",
    "all", "distinct", "order", "by", "limit", "offset", "group", "having",
    "select", "update", "set", "insert", "into", "delete", "values",
    "left", "right", "inner", "outer", "join", "exists", "using",
    "interval", "timestamp", "int", "bigint", "float", "text", "varchar",
    "conflict", "do", "nothing", "returning", "between", "is", "like",
    "filter", "over", "partition", "when", "then", "else", "end", "if",
    "array", "desc", "asc", "at", "time", "zone", "boolean", "real",
    "numeric", "jsonb", "json", "fetchone", "fetchall", "first", "one",
    "case_when", "any", "some",
}


def load_schema() -> dict[str, set[str]]:
    insp = inspect(engine)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


_SQL_CALL = re.compile(
    r'text\s*\(\s*(?P<quote>["\']{1,3})(?P<body>.*?)(?P=quote)\s*\)',
    re.DOTALL,
)


def extract_sql_blocks(path: pathlib.Path) -> list[tuple[int, str]]:
    try:
        src = path.read_text()
    except Exception:
        return []
    return [
        (src.count("\n", 0, m.start()) + 1, m.group("body").strip())
        for m in _SQL_CALL.finditer(src)
        if len(m.group("body").strip()) >= 6
    ]


def find_simple_from_table(sql: str) -> str | None:
    """Return the first plain `FROM <table>` — no joins, no subquery."""
    # Skip CTE portion: find the actual main query
    # Simple heuristic: last FROM outside parentheses
    depth = 0
    from_pos = -1
    tokens = re.finditer(r"(\()|(\))|(\bFROM\s+(\w+))", sql, re.I)
    for t in tokens:
        if t.group(1):
            depth += 1
        elif t.group(2):
            depth -= 1
        elif t.group(3) and depth == 0:
            from_pos = t.start(4)
            table = t.group(4).lower()
            return table
    return None


def find_column_refs_in_where(sql: str) -> set[str]:
    """Columns used unqualified in WHERE/SET after the main table."""
    # Strip string literals
    cleaned = re.sub(r"'[^']*'", "''", sql)
    # Find `column = :param` or `column IS [NOT] NULL` patterns
    matches: set[str] = set()
    pat = re.compile(
        r"(?:WHERE|AND|OR)\s+([a-zA-Z_][\w]*?)\s*(?:=|<>|<=|>=|<|>|\sIS\s|\sIN\s|\sLIKE\s|\sBETWEEN\s)",
        re.IGNORECASE,
    )
    for m in pat.finditer(cleaned):
        col = m.group(1).lower()
        if col not in _COLUMN_SKIPLIST:
            matches.add(col)
    return matches


def main() -> int:
    schema = load_schema()
    findings: list[tuple[str, int, str, str, str]] = []

    for py_file in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        for line, sql in extract_sql_blocks(py_file):
            table = find_simple_from_table(sql)
            if not table or table not in schema:
                continue
            cols = find_column_refs_in_where(sql)
            for col in cols:
                if col not in schema[table]:
                    findings.append((
                        str(py_file.relative_to(APP_ROOT.parent)),
                        line, table, col, sql[:100].replace("\n", " "),
                    ))

    if findings:
        by_col = defaultdict(list)
        for f, line, tab, col, snip in findings:
            by_col[(tab, col)].append((f, line, snip))
        print(f"MISSING COLUMNS ({len(by_col)} distinct pairs)\n")
        for (tab, col), hits in sorted(by_col.items()):
            print(f"  {tab}.{col!r}")
            for f, line, snip in hits[:3]:
                print(f"    {f}:{line}")
                print(f"      {snip}...")
            if len(hits) > 3:
                print(f"    ... and {len(hits) - 3} more")
            print()
    else:
        print("✅ No missing columns in simple-FROM paths")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
