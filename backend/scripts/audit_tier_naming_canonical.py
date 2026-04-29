#!/usr/bin/env python3
"""
audit_tier_naming_canonical.py — enforce canonical tier naming on
user-facing surfaces.

Born 2026-04-23 after a tier-rename incident. A stale memo had
a wrong premise about the entry-tier label, and a 15-file rename
plan was built on that premise without running the cheap
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
- Existing audit `audit_landing_lite_shipped.py` enforces every
  Lite-card bullet maps to a shipped dashboard component.

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
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LANDING_PATH = REPO_ROOT / "dashboard" / "src" / "app" / "page.tsx"
DASHBOARD_APP_PAGE = REPO_ROOT / "dashboard" / "src" / "app" / "app" / "page.tsx"
USE_SESSION = REPO_ROOT / "dashboard" / "src" / "app" / "lib" / "useSession.ts"
BACKEND_MERCHANT_API = REPO_ROOT / "backend" / "app" / "api" / "merchant.py"
BACKEND_LIVE_ALERTS = REPO_ROOT / "backend" / "app" / "api" / "live_alerts.py"

# (file_path, substring, semantic reason shown on failure)
_REQUIRED: list[tuple[Path, str, str]] = [
    # Landing — single source of truth for user-facing tier names
    (LANDING_PATH,         'key: "lite"',                "landing entry tier machine id"),
    (LANDING_PATH,         'label: "Lite"',              "landing entry tier user-facing label"),
    (LANDING_PATH,         'key: "pro"',                 "landing mid tier machine id"),
    (LANDING_PATH,         'key: "scale"',               "landing top tier machine id"),
    # Dashboard tier-state type — must match landing taxonomy
    (DASHBOARD_APP_PAGE,   '"lite" | "pro" | "scale"',   "dashboard tier state union"),
    (USE_SESSION,          '"lite" | "pro" | "scale"',   "useSession Tier type"),
    # Backend response contract — /merchant/me normaliser fallback to lite
    (BACKEND_MERCHANT_API, 'return "lite"',              "merchant.py normalise_plan fallback"),
    # Backend Pydantic class names (dashboard api-types mirrors these)
    (BACKEND_LIVE_ALERTS,  "class LiteAlertRow(",        "live_alerts Pydantic entry-tier class"),
    (BACKEND_LIVE_ALERTS,  "class LiteAlertsResponse(",  "live_alerts Pydantic entry-tier response"),
]


@telemetered("audit_tier_naming_canonical")
def main(argv: list[str]) -> int:
    strict = "--strict" in argv

    missing: list[tuple[Path, str, str]] = []
    unreadable: list[Path] = []

    for path, needle, reason in _REQUIRED:
        if not path.exists():
            unreadable.append(path)
            continue
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            unreadable.append(path)
            continue
        if needle not in text:
            missing.append((path, needle, reason))

    if not missing and not unreadable:
        print(
            f"audit_tier_naming_canonical: clean — {len(_REQUIRED)}/{len(_REQUIRED)} "
            "canonical anchors present across landing + dashboard + backend"
        )
        return 0

    print("audit_tier_naming_canonical: FAIL — canonical tier naming drift detected")
    print()

    if unreadable:
        print("Unreadable / missing files:")
        for p in unreadable:
            print(f"  - {p}")
        print()

    if missing:
        print("Missing canonical anchors:")
        by_file: dict[Path, list[tuple[str, str]]] = {}
        for path, needle, reason in missing:
            by_file.setdefault(path, []).append((needle, reason))
        for path, entries in by_file.items():
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:")
            for needle, reason in entries:
                print(f"    - {needle!r:40s}  ({reason})")
        print()

    print("Canonical tier naming is Lite / Pro / Scale (2026-04-23).")
    print("If this is an intentional rename by the founder, update ALL:")
    print("  1. This script's _REQUIRED list")
    print("  2. The memory file project_tier_rename_dashboard_backlog.md")
    print("  3. The audit_landing_lite_shipped.py header comment")
    print("  4. Every file flagged above, coherently, in the same commit")
    print()
    print("If this is NOT an intentional rename, ABORT and investigate:")
    print("  grep -n 'key: \"lite\"\\|label: \"Lite\"' dashboard/src/app/page.tsx")
    print("  git log --oneline -- dashboard/src/app/page.tsx | head -5")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
