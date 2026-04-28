#!/usr/bin/env python3
"""audit_orphan_card_components.py — preflight preventer for the
"shipped backend + tests, frontend orphan" theater pattern.

Born 2026-04-28 night after founder hardcore parity audit caught
two cards (`StockHealthCard.tsx`, `HowCustomersFindYouCard.tsx`)
that had:
  - Component file with full implementation
  - Test file
  - Backend endpoints live (200 in production)
  - Visual specs documented under /docs/

…but ZERO references in `dashboard/src/app/app/page.tsx` or any
other surface in `dashboard/src/`. The merchant on /app/lite
literally could NOT see the feature despite all the data being
collected and the test suite passing. Three turns ago I had
claimed both shipped at "strict 10/10" — they hadn't.

THE RULE:
  - Any `dashboard/src/app/components/*Card.tsx` that has a sibling
    `*Card.test.tsx` (intent-to-ship signal) MUST be imported by at
    least one OTHER `.tsx` file in `dashboard/src/`.
  - A test file alone is not enough — tests can pass on a component
    that's never rendered.

Exit non-zero on violation so the pre-commit hook refuses the commit.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPONENTS_DIR = REPO_ROOT / "dashboard" / "src" / "app" / "components"
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"


def main() -> int:
    if not COMPONENTS_DIR.is_dir():
        print(f"audit_orphan_card_components: {COMPONENTS_DIR} not found — skipping")
        return 0

    orphans: list[str] = []
    inspected = 0

    for tsx in COMPONENTS_DIR.glob("*Card.tsx"):
        name = tsx.stem
        if name.endswith(".test"):
            continue
        test_file = tsx.with_name(f"{name}.test.tsx")
        # Only flag components that have a test file — tests are the
        # explicit intent-to-ship signal. A component with no test is
        # often work-in-progress, not yet a parity claim.
        if not test_file.exists():
            continue
        inspected += 1

        # Search every other .tsx for an import or reference to this name.
        used = False
        for candidate in DASHBOARD_SRC.rglob("*.tsx"):
            if candidate == tsx or candidate == test_file:
                continue
            try:
                if name in candidate.read_text(encoding="utf-8", errors="ignore"):
                    used = True
                    break
            except OSError:
                continue

        if not used:
            orphans.append(name)

    if orphans:
        print(
            f"\033[31maudit_orphan_card_components: {len(orphans)} orphan Card "
            f"component(s) — have tests but never imported\033[0m"
        )
        for o in orphans:
            rel = (COMPONENTS_DIR / f"{o}.tsx").relative_to(REPO_ROOT)
            print(f"  - {rel}")
        print(
            "\n  Pattern: a Card component with a sibling .test.tsx file but\n"
            "  NO consumer anywhere in dashboard/src/ is theater — the merchant\n"
            "  cannot see this feature. Either (a) wire the component into the\n"
            "  appropriate render surface (page.tsx for /app/lite|pro|scale,\n"
            "  FloorLayout-wrapped page for sub-routes), or (b) delete the\n"
            "  component + test if the feature was abandoned.\n"
            "\n"
            "  This bug class was caught 2026-04-28 night after a parity audit\n"
            "  found StockHealthCard + HowCustomersFindYouCard had been authored\n"
            "  but never rendered, despite \"shipped strict 10/10\" claims.\n"
        )
        return 1

    print(f"audit_orphan_card_components: clean — {inspected} cards with tests, all consumed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
