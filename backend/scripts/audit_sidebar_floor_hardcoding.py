#!/usr/bin/env python3
"""audit_sidebar_floor_hardcoding.py — preflight preventer for the
Sidebar `currentFloor=` floor-highlight regression.

Born 2026-04-28 night after founder catch: clicking "Pro" lit "Lite"
because `dashboard/src/app/app/page.tsx` hardcoded
`currentFloor="pulse"` while serving as the SHARED backing component
for /app/lite, /app/pro, and /app/scale via re-export shims:

    /app/lite/page.tsx  → export { default } from "../page";
    /app/pro/page.tsx   → export { default } from "../page";

Single component, three URLs, one hardcoded string → wrong tab lit
on two of the three routes.

THE RULE:
  - `<Sidebar currentFloor="..."/>` (string-literal value) is BANNED
    in `dashboard/src/app/app/page.tsx` because that file is
    re-exported across multiple floor routes. The value MUST be
    derived from `usePathname()` (or equivalent) so the tab highlight
    follows the URL.
  - `<Sidebar currentFloor={...}/>` (expression) is fine.
  - `FloorLayout floor="..."` (string-literal) IS fine because each
    floor page maps 1:1 to a route — no re-export ambiguity.

Exit non-zero on violation so the pre-commit hook refuses the commit.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PAGE = REPO_ROOT / "dashboard" / "src" / "app" / "app" / "page.tsx"

# Match `currentFloor="..."` — quoted string literal (the bug pattern).
# Reject these in SHARED_PAGE; allow `currentFloor={...}` (expression).
_HARDCODE_RE = re.compile(r'currentFloor\s*=\s*"[^"]*"')


def main() -> int:
    if not SHARED_PAGE.is_file():
        print(f"audit_sidebar_floor: {SHARED_PAGE} not found — skipping")
        return 0

    text = SHARED_PAGE.read_text(encoding="utf-8")
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Skip comment lines (the historical comment in the fix is OK).
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if _HARDCODE_RE.search(line):
            findings.append((lineno, line.rstrip()))

    if findings:
        print(
            f"\033[31maudit_sidebar_floor: hardcoded currentFloor= in shared backing component\033[0m"
        )
        print(f"  file: {SHARED_PAGE.relative_to(REPO_ROOT)}")
        for lineno, line in findings:
            print(f"  L{lineno}: {line}")
        print(
            "\n  This file is re-exported across /app/lite, /app/pro, /app/scale\n"
            "  via shim files. A hardcoded string-literal currentFloor lights\n"
            "  the WRONG floor tab on 2 of the 3 routes (founder-caught\n"
            "  regression 2026-04-28 night).\n"
            "\n"
            "  Fix: derive sidebarCurrentFloor from usePathname():\n"
            '    const pathname = usePathname();\n'
            '    const sidebarCurrentFloor: "pulse" | "intelligence" | "operations" =\n'
            '      pathname?.startsWith("/app/pro") ? "intelligence"\n'
            '      : pathname?.startsWith("/app/scale") ? "operations"\n'
            '      : "pulse";\n'
            '    <Sidebar currentFloor={sidebarCurrentFloor} ... />\n'
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
