#!/usr/bin/env python3
"""JSONB array-length guard preventer.

Born 2026-04-27 from Gap #8 close DA-loop sibling hunt. Caught a latent
class: psycopg2 converts Python `None` to JSON null literal (`'null'::jsonb`)
on JSONB column inserts under some path configurations — NOT to SQL NULL.
SQL `IS NULL` does NOT match JSON null. Then `jsonb_array_length(<scalar>)`
panics with "cannot get array length of a scalar".

The bug is latent: tests pass when fixtures use SQL NULL, breaks when
real merchants happen to have JSON null literal stored. It DID surface
in test_first_discount_none_bucket of cohort-by-dimension and was fixed
across 9 sibling sites in lite_extras.py + conversion_metrics.py +
ltv_engine.py.

This preventer scans for `jsonb_array_length(...)` calls in raw SQL text
and asserts a `jsonb_typeof(<same_expr>) = 'array'` guard appears within
4 lines BEFORE the call (same SQL block).

Pre-flight blocker.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path("/opt/wishspark/backend")

# Files we scan: every .py under app/
SCAN_ROOTS = [BACKEND / "app"]
EXCLUDE_DIRS = {".venv", "venv", "__pycache__"}

# Pattern: jsonb_array_length(<EXPR>) where EXPR is the column or table.col
_JSONB_ARRAY_LEN_RE = re.compile(
    r"jsonb_array_length\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)"
)
# Accepts both positive guard (`= 'array'`) and negative guard
# (`<> 'array'` which short-circuits on non-array via CASE/WHEN)
_JSONB_TYPEOF_RE = re.compile(
    r"jsonb_typeof\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)\s*(?:=|<>|!=)\s*'array'"
)


def _scan_file(path: Path) -> list[str]:
    """Return list of finding strings for unguarded jsonb_array_length calls."""
    findings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return findings

    for idx, line in enumerate(lines):
        for m in _JSONB_ARRAY_LEN_RE.finditer(line):
            expr = m.group(1)
            # Look back up to 4 lines for jsonb_typeof(expr) = 'array'
            window_start = max(0, idx - 4)
            window = "\n".join(lines[window_start:idx + 1])
            guarded = any(
                tm.group(1) == expr
                for tm in _JSONB_TYPEOF_RE.finditer(window)
            )
            # Also: comments and explanatory docstrings are exempt
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("--"):
                continue
            if guarded:
                continue
            findings.append(
                f"{path.relative_to(BACKEND)}:{idx + 1}: "
                f"jsonb_array_length({expr}) without preceding "
                f"jsonb_typeof({expr}) = 'array' guard within 4 lines"
            )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on any finding (default: lenient)")
    args = ap.parse_args()

    all_findings: list[str] = []
    files_scanned = 0
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            files_scanned += 1
            all_findings.extend(_scan_file(p))

    if not all_findings:
        print(
            f"audit_jsonb_array_length_guard: OK — "
            f"{files_scanned} files scanned, all jsonb_array_length() "
            f"calls have jsonb_typeof guard"
        )
        return 0

    print(
        f"audit_jsonb_array_length_guard: FAIL — "
        f"{len(all_findings)} unguarded jsonb_array_length() call(s)"
    )
    print()
    for f in all_findings:
        print(f"  {f}")
    print()
    print("Fix: add `AND jsonb_typeof(<column>) = 'array'` BEFORE the")
    print("`jsonb_array_length(<column>)` call (same WHERE/CASE block,")
    print("within 4 lines). Background: psycopg2 may convert Python None")
    print("to JSON null literal (a JSONB scalar) instead of SQL NULL on")
    print("JSONB column insert; SQL `IS NULL` does NOT catch JSON null,")
    print("so the unguarded jsonb_array_length(scalar) call panics.")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
