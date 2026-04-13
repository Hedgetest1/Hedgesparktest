#!/usr/bin/env python
"""
audit_timezone.py — Find naive/aware datetime mismatches.

Python raises `TypeError: can't compare offset-naive and offset-aware`
when you compare a naive datetime to an aware one — BUT in practice
most code wraps the comparison in try/except, so the mismatch becomes
a silent bug: the path that was supposed to filter on "last 24h" just
crashes and returns 0/empty.

Categories:
  1. `datetime.utcnow()` — deprecated, returns naive
  2. `datetime.now(timezone.utc).replace(tzinfo=None)` — deliberate
     naive, often used to match DB columns that are TIMESTAMP WITHOUT
     TIME ZONE. OK if consistent.
  3. Mixing `datetime.now()` (naive) with `datetime.now(timezone.utc)`
     (aware) inside the same module — smell.
  4. Comparing epoch millis (int) to datetime — type bug, the SQL
     will work via cast but Python code will crash.

We flag (1) as a deprecation and (3) as a smell. (4) we can't static-
analyze reliably.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import defaultdict

APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


class Finding:
    __slots__ = ("file", "line", "kind", "detail")

    def __init__(self, file: str, line: int, kind: str, detail: str):
        self.file = file
        self.line = line
        self.kind = kind
        self.detail = detail


def audit_file(path: pathlib.Path) -> list[Finding]:
    try:
        src = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    out: list[Finding] = []
    rel = str(path.relative_to(APP_ROOT.parent))

    has_utcnow = False
    has_naive_now = False
    has_aware_now = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # datetime.utcnow
            if (
                node.attr == "utcnow"
                and isinstance(node.value, ast.Name)
                and node.value.id == "datetime"
            ):
                has_utcnow = True
                out.append(Finding(
                    rel, node.lineno, "utcnow_deprecated",
                    "datetime.utcnow() is deprecated — returns naive UTC",
                ))
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "now"
                and isinstance(func.value, ast.Name)
                and func.value.id == "datetime"
            ):
                if not node.args:
                    has_naive_now = True
                else:
                    has_aware_now = True

    if has_naive_now and has_aware_now:
        out.append(Finding(
            rel, 1, "naive_aware_mix",
            "this file uses BOTH datetime.now() (naive) and datetime.now(tz) (aware)",
        ))

    return out


def main() -> int:
    by_kind: dict[str, list[Finding]] = defaultdict(list)
    for py in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        for f in audit_file(py):
            by_kind[f.kind].append(f)

    if not any(by_kind.values()):
        print("✅ No timezone smells found.")
        return 0

    print("TIMEZONE AUDIT FINDINGS\n")
    for kind in ("utcnow_deprecated", "naive_aware_mix"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        print(f"{kind} ({len(items)})")
        by_file: dict[str, list[int]] = defaultdict(list)
        for f in items:
            by_file[f.file].append(f.line)
        for file, lines in sorted(by_file.items()):
            shown = ", ".join(str(l) for l in lines[:6])
            if len(lines) > 6:
                shown += f", +{len(lines) - 6} more"
            print(f"  {file}  [{shown}]")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
