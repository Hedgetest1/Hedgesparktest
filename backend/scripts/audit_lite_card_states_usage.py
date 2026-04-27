#!/usr/bin/env python3
"""Lite/Cassettone state-primitive enforcement preventer.

Born 2026-04-27 after Phase 1 cosmetic audit found `LiteBaseAnalytics.tsx`
had rolled its own `TileSkeleton` + `TileError` without the role +
aria-live attributes that the canonical `_CardStates.tsx` primitive
ships. Screen readers got silence on loading + no live announcement on
errors. Same regression class flagged in CLAUDE.md §4 ("Every Pro card
MUST use the unified error/loading/empty primitives from `_CardStates.tsx`").

Without this preventer, the next time a developer adds a Lite or
Cassettone component with `apiClient.GET(...)` and rolls their own
skeleton/error UI, the regression slips through review (the missing
a11y attrs are invisible at PR-glance time).

Scope
=====
Files matching:
  - dashboard/src/app/components/Lite*.tsx
  - dashboard/src/app/components/Cassettone*.tsx

Detection rule
==============
For each in-scope file that calls `apiClient.GET(` (i.e. fetches data
from the backend), the file MUST import at least one of:
  - From `_CardStates`: CardSkeleton / CardError / CardEmpty / useCardFetch
  - From `LiteBaseAnalytics`: TileSkeleton / TileError / TileEmpty
  - The canonical primitive itself (LiteBaseAnalytics.tsx defines
    TileSkeleton/Error/Empty inline — exempt from import requirement
    since it IS the source).

If a file fetches data but imports neither, it's likely rolling its
own UI for at least one of {loading, error, empty} state — flag.

Exemption
=========
- Files explicitly exempt: LiteBaseAnalytics.tsx (the source), files
  containing the inline marker comment `// audit:card-states-ok`
- Files that don't call apiClient.GET (no async state to surface)

How to fix
==========
Either import the canonical primitive:

    import { CardSkeleton, CardError, CardEmpty, useCardFetch } from "./_CardStates";

Or for compact tile usage inside larger Lite sections, reuse:

    import { TileSkeleton, TileError, TileEmpty } from "./LiteBaseAnalytics";

If you genuinely need a one-off custom state UI (rare), add inline:

    // audit:card-states-ok (reason: ...)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DASHBOARD = Path("/opt/wishspark/dashboard/src")
COMPONENTS_DIR = DASHBOARD / "app" / "components"

# Files in scope: Lite* + Cassettone*
def in_scope_files() -> list[Path]:
    files: list[Path] = []
    if not COMPONENTS_DIR.exists():
        return files
    for pattern in ("Lite*.tsx", "Cassettone*.tsx"):
        files.extend(sorted(COMPONENTS_DIR.glob(pattern)))
    # Skip test files
    return [f for f in files if not f.name.endswith(".test.tsx")]

# Files explicitly exempt (the canonical primitive sources themselves)
EXEMPT_FILES = {
    "LiteBaseAnalytics.tsx",  # defines TileSkeleton/Error/Empty itself
}

# Patterns. Match either inline `apiClient.GET(` or the chained
# multi-line form (`apiClient` on one line then `.GET(` on the next).
# Both are common in this codebase.
USES_API_CLIENT = re.compile(r'apiClient[\s\.]+\.?GET\s*\(', re.DOTALL)

# Acceptable imports — at least one of these proves the file uses
# canonical primitives (or the cassettone variant of them).
IMPORTS_CANONICAL = re.compile(
    r'from\s+["\'][./]*(?:_CardStates|LiteBaseAnalytics)["\']'
)

# Inline exemption marker
INLINE_EXEMPT = re.compile(r'//\s*audit:card-states-ok')


def scan_file(path: Path) -> list[str]:
    """Return list of human-readable findings for the file."""
    findings: list[str] = []
    if path.name in EXEMPT_FILES:
        return findings

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return findings

    if INLINE_EXEMPT.search(text):
        return findings

    if not USES_API_CLIENT.search(text):
        # No fetch in this file — no async state to surface, not in scope
        return findings

    if not IMPORTS_CANONICAL.search(text):
        rel = path.relative_to(DASHBOARD.parent.parent)
        findings.append(
            f"{rel}: fetches via apiClient.GET but imports neither _CardStates "
            f"nor LiteBaseAnalytics primitives — likely rolling own loading/error UI"
        )

    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on findings (default: lenient).")
    args = ap.parse_args()

    files = in_scope_files()
    all_findings: list[str] = []
    for f in files:
        all_findings.extend(scan_file(f))

    if not all_findings:
        print(f"audit_lite_card_states_usage: OK — {len(files)} Lite/Cassettone files scanned, all use canonical state primitives")
        return 0

    print(f"audit_lite_card_states_usage: FAIL — {len(all_findings)} file(s) with own-rolled state UI")
    print()
    for finding in all_findings:
        print(f"  {finding}")
    print()
    print("Fix: import from _CardStates (CardSkeleton/CardError/CardEmpty/useCardFetch)")
    print("OR from LiteBaseAnalytics (TileSkeleton/TileError/TileEmpty for compact tiles)")
    print("OR annotate intentional one-off: `// audit:card-states-ok (reason: ...)`")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
