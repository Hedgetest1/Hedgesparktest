#!/usr/bin/env python3
"""
audit_tier_naming_canonical.py — enforce canonical tier naming on
user-facing surfaces.

Born 2026-04-23 after the Lite→Starter rename incident. The
memory `project_tier_rename_dashboard_backlog.md` had a stale
premise claiming the landing was renamed to "Starter". A 15-file
rename plan was built on that premise without running the cheap
disconfirming grep. Recovery cost: ~30 min of work reverted + one
trust-eroding apology to the founder.

This audit is the structural pipeline-recognition preventer for
that exact bug class: the landing page.tsx is the single source
of truth for tier names, and this script asserts the canonical
values are present. If a future session attempts to flip the entry
tier away from "Lite", preflight blocks the commit with a direct
link to the memory explaining WHY.

Canonical as of 2026-04-23, confirmed by:
- `git log` commit `eb1b50d feat(naming): canonicalize tier names
  Lite / Pro / Scale across UI`
- Founder directive 2026-04-23: "l'entry level si chiama Lite,
  1M volte"
- Existing audit `audit_landing_starter_shipped.py` header comment:
  "canonical tier name is now `Lite` per founder directive 2026-04-20"

Checks
------
Required substrings in `dashboard/src/app/page.tsx`:
- `key: "lite"`       — entry tier machine id
- `label: "Lite"`     — entry tier user-facing label
- `key: "pro"`        — mid tier
- `key: "scale"`      — top tier

If any of the above is missing, the commit is blocked with the
canonical-naming reminder.

The check is intentionally positive-only (we assert what MUST be
present) rather than negative (we don't forbid every possible
rename target). A future legitimate rename by the founder will
delete the `key: "lite"` substring → this audit fails → the
session must explicitly update this file + the related memory
before the rename can ship. That human-in-the-loop moment is
exactly what was missing on 2026-04-23.

Exit codes
----------
  0  canonical naming intact
  1  at least one canonical check failed

Usage
-----
    ./scripts/audit_tier_naming_canonical.py           # report
    ./scripts/audit_tier_naming_canonical.py --strict  # exit 1 on any fail
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LANDING_PATH = REPO_ROOT / "dashboard" / "src" / "app" / "page.tsx"

# (substring, semantic reason shown on failure)
_REQUIRED_LANDING_SUBSTRINGS: list[tuple[str, str]] = [
    ('key: "lite"',   "entry tier machine id"),
    ('label: "Lite"', "entry tier user-facing label"),
    ('key: "pro"',    "mid tier machine id"),
    ('key: "scale"',  "top tier machine id"),
]


def main(argv: list[str]) -> int:
    strict = "--strict" in argv

    if not LANDING_PATH.exists():
        print(
            f"audit_tier_naming_canonical: FAIL — landing page not found at {LANDING_PATH}",
            file=sys.stderr,
        )
        return 1

    try:
        text = LANDING_PATH.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"audit_tier_naming_canonical: FAIL — could not read landing: {exc}",
            file=sys.stderr,
        )
        return 1

    missing: list[tuple[str, str]] = []
    for needle, reason in _REQUIRED_LANDING_SUBSTRINGS:
        if needle not in text:
            missing.append((needle, reason))

    if not missing:
        print(
            "audit_tier_naming_canonical: clean — Lite/Pro/Scale canonical "
            "(4/4 substrings present in landing)"
        )
        return 0

    print("audit_tier_naming_canonical: FAIL — canonical tier naming drift detected")
    print()
    print("Missing from dashboard/src/app/page.tsx:")
    for needle, reason in missing:
        print(f"  - {needle!r:30s}  ({reason})")
    print()
    print("Canonical tier naming is Lite / Pro / Scale (2026-04-23).")
    print("If this is an intentional rename by the founder, update BOTH:")
    print("  1. This script's _REQUIRED_LANDING_SUBSTRINGS list")
    print("  2. The memory file project_tier_rename_dashboard_backlog.md")
    print("  3. The audit_landing_starter_shipped.py header comment")
    print("...in the same commit.")
    print()
    print("If this is NOT an intentional rename, ABORT and investigate:")
    print("  grep -n 'key: \"lite\"\\|label: \"Lite\"' dashboard/src/app/page.tsx")
    print("  git log --oneline -- dashboard/src/app/page.tsx | head -5")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
