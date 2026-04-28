#!/usr/bin/env python3
"""audit_cte_missing_comma.py — preflight preventer for CTE-chain
missing-comma SQL syntax errors.

Born 2026-04-28 night after founder caught 3 Pro cards stuck on
"Couldn't load this card". Backend logs revealed
`/orders/product-conversions` had been emitting 500 errors all day:

    psycopg2.errors.SyntaxError: syntax error at or near "converted"
    LINE 82:                 converted AS (
                             ^

Root cause: `app/api/orders.py` had a CTE chain:

    pid_to_url AS (
        SELECT DISTINCT ON (product_id) ...
        ORDER BY product_id, timestamp DESC
    )                       -- ← MISSING COMMA HERE
    converted AS (
        ...
    )

Postgres parsed `pid_to_url AS (...)` and then expected the main
SELECT, but found another `NAME AS (` instead → syntax error.

THE RULE:
  - Any line consisting solely of `)` (the close of a non-terminal
    CTE), followed only by blank lines / comments / whitespace, then
    `<NAME> AS (` → MUST have a comma after the `)`.
  - The terminal CTE (last before the main `SELECT`) does NOT have a
    trailing comma — the regex only flags chains where another
    `NAME AS (` follows.

Exit non-zero on violation so the pre-commit hook refuses the commit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_APP = REPO_ROOT / "backend" / "app"

# Match: a line whose stripped content is exactly ')' — the close of
# a CTE block — followed by blank lines / SQL line-comments — then
# `<NAME> AS (`. The unbroken `)` (without a trailing comma) means
# this CTE chain is broken.
_PATTERN = re.compile(
    r"^\s*\)\s*$\n(?:\s*$\n|\s*--[^\n]*$\n)*\s*(\w+)\s+AS\s*\(",
    re.MULTILINE,
)


def main() -> int:
    if not BACKEND_APP.is_dir():
        print(f"audit_cte_missing_comma: {BACKEND_APP} not found — skipping")
        return 0

    findings: list[tuple[Path, int, str]] = []
    scanned = 0
    for path in BACKEND_APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "AS (" not in text:
            continue
        scanned += 1
        for match in _PATTERN.finditer(text):
            # Extra guard: only flag positions inside a `WITH` chain.
            # Look back ~3000 chars; if no `WITH` keyword, this isn't
            # a CTE — could be a CHECK constraint, view, etc.
            prefix = text[: match.start()]
            if "WITH" not in prefix[-3000:]:
                continue
            line_no = prefix.count("\n") + 1
            findings.append((path, line_no, match.group(1)))

    if findings:
        print(
            "\033[31maudit_cte_missing_comma: CTE chain missing comma between consecutive AS blocks\033[0m"
        )
        for path, line_no, next_name in findings:
            rel = path.relative_to(REPO_ROOT)
            print(
                f"  {rel}:{line_no} → next CTE {next_name!r} has no comma "
                f"after the preceding ')'"
            )
        print(
            "\n  Postgres parses `<prev_cte> AS (...)` and then expects the\n"
            "  main SELECT. Finding another `NAME AS (` instead is a\n"
            "  syntax error at runtime — the endpoint returns 500 silently\n"
            "  to the dashboard.\n"
            "\n"
            "  Fix: add a comma after the `)` that closes the previous\n"
            "  CTE block. The terminal CTE (last before main SELECT) is\n"
            "  the only one that has no trailing comma.\n"
        )
        return 1

    print(f"audit_cte_missing_comma: clean — {scanned} files scanned, 0 missing-comma chains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
