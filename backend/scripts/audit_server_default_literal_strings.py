#!/usr/bin/env python3
"""audit_server_default_literal_strings.py — block SQLAlchemy
`server_default="<sql_func>()"` literal-string drift.

Born 2026-05-07 closing the bug class memoized in
`project_server_default_now_literal_bug_2026_05_07.md`. SQLAlchemy
renders `server_default="now()"` as `DEFAULT 'now()'` (literal STRING)
in the generated DDL, not `DEFAULT now()` (SQL function call). Prod
tables (created via legacy `Base.metadata.create_all`) silently
dropped the broken default; fresh deploys via alembic upgrade would
have received the literal-string default and broken on every INSERT
that relied on a missing value.

This audit fails preflight when any `Column(..., server_default="<X>")`
contains a SQL-function-shaped value. Allowed:
  - `server_default=text("now()")` — proper SQL expression wrap
  - `server_default="0"` / `"1"` / `"active"` / `"false"` — plain
    constant defaults (literal numeric/string DDL DEFAULT)
  - `server_default="{}"` / `"[]"` — JSON literal defaults

Forbidden:
  - `server_default="now()"` / `"gen_random_uuid()"` / `"uuid_generate_v4()"`
    / `"currval(...)"` — SQL function shapes that need text() wrap.

Wired into preflight + invariant_monitor.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "app" / "models"

# A SQL-function-shaped string literal is a string that contains `(` and `)`
# and matches a function-name pattern. We opt-list the lower-case identifier
# pattern (no spaces, only func-call shape) to avoid flagging legit JSON or
# plain-text defaults.
_SQL_FUNC_SHAPE = re.compile(r'^[a-z_][a-z0-9_]*\([^)]*\)$', re.IGNORECASE)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    src = safe_read_text(path)
    if src is None:
        return findings
    if "server_default" not in src:
        return findings
    try:
        tree = ast.parse(src, str(path))
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "server_default":
                continue
            v = kw.value
            # Only flag bare string constants — text() / Identifier / etc. are OK.
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                literal = v.value
                if _SQL_FUNC_SHAPE.match(literal):
                    findings.append((node.lineno, literal))
    return findings


def main() -> int:
    if not MODELS.is_dir():
        print(f"audit_server_default_literal_strings: skip — {MODELS} missing")
        return 0
    bad: list[str] = []
    for py in sorted(MODELS.rglob("*.py")):
        for lineno, literal in _scan_file(py):
            rel = py.relative_to(REPO)
            bad.append(f"  {rel}:{lineno}  server_default=\"{literal}\"")
    if not bad:
        print("audit_server_default_literal_strings: clean — every "
              "SQL-function server_default is text()-wrapped.")
        return 0
    print(
        f"audit_server_default_literal_strings: FAIL — "
        f"{len(bad)} literal-string SQL-function default(s) found:"
    )
    for line in bad:
        print(line)
    print()
    print(
        "Fix: wrap with sqlalchemy.text(...). Example:\n"
        "  WRONG:  Column(..., server_default=\"now()\")\n"
        "  RIGHT:  from sqlalchemy import text\n"
        "          Column(..., server_default=text(\"now()\"))\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
