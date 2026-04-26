#!/usr/bin/env python3
"""Lite-floor nav vs section parity preventer.

Born 2026-04-26 after founder reported the sidebar "going back to LITE"
when scrolling past `lite-refunds` and `lite-audience` — those (and
`lite-today`, `lite-last7`) had been added as sections on /app/lite
WITHOUT being added to NAV_ITEMS_LITE or SECTION_TO_NAV. Result: the
IntersectionObserver fires `setActiveSection("lite-refunds")` but no
nav item matches → no highlight → visual regression where the
sidebar appears to lose the user's scroll position.

This audit fails if:
- page.tsx (or any component imported into the Lite floor) renders a
  `section-lite-*` anchor whose stripped id is not present in
  NAV_ITEMS_LITE in Sidebar.tsx
- That same id is not present as a key in SECTION_TO_NAV

Catches the next "I added a Lite section but forgot to wire the nav"
regression at preflight time — before it ships and a merchant
notices the sidebar going dead on their scroll.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DASHBOARD = Path("/opt/wishspark/dashboard/src")
PAGE_TSX = DASHBOARD / "app" / "app" / "page.tsx"
SIDEBAR_TSX = DASHBOARD / "app" / "components" / "Sidebar.tsx"
COMPONENTS_DIR = DASHBOARD / "app" / "components"

# Live Radar sits at the bottom of the Lite vertical and uses the
# canonical `live` nav id (shared with Pro NAV_ITEMS). It is NOT a
# `section-lite-*` anchor — keep it whitelisted so the audit does
# not flag the legitimate cross-floor reuse.
NAV_ID_WHITELIST = {"live"}

SECTION_RE = re.compile(r'id="section-(lite-[a-z0-9]+)"')
NAV_ITEM_RE = re.compile(r'id:\s*"(lite-[a-z0-9]+)"')
NAV_MAP_RE = re.compile(r'"(lite-[a-z0-9]+)"\s*:')


def collect_section_ids() -> set[str]:
    ids: set[str] = set()
    for path in [PAGE_TSX, *COMPONENTS_DIR.glob("Lite*.tsx")]:
        if not path.exists():
            continue
        ids.update(SECTION_RE.findall(path.read_text()))
    return ids


def collect_nav_ids() -> tuple[set[str], set[str]]:
    src = SIDEBAR_TSX.read_text()
    # NAV_ITEMS_LITE block bracketed by `const NAV_ITEMS_LITE` and
    # closing `];` on its own line (or paragraph break before
    # SECTION_TO_NAV). We use a simple substring slice between markers.
    nav_block_start = src.find("const NAV_ITEMS_LITE")
    section_map_start = src.find("const SECTION_TO_NAV")
    if nav_block_start == -1 or section_map_start == -1:
        sys.stderr.write("ERROR: Sidebar.tsx structure changed — cannot parse markers.\n")
        return set(), set()
    nav_block = src[nav_block_start:section_map_start]
    nav_ids = set(NAV_ITEM_RE.findall(nav_block))

    section_map_end = src.find("};", section_map_start)
    section_block = src[section_map_start:section_map_end]
    section_map_keys = set(NAV_MAP_RE.findall(section_block))
    return nav_ids, section_map_keys


def main() -> int:
    section_ids = collect_section_ids()
    nav_ids, section_map_keys = collect_nav_ids()

    # Strip `lite-` prefix is kept for symmetry — section_ids already
    # stripped the `section-` prefix via the regex group.
    missing_in_nav = (section_ids - nav_ids) - NAV_ID_WHITELIST
    missing_in_map = (section_ids - section_map_keys) - NAV_ID_WHITELIST
    orphan_nav = (nav_ids - section_ids) - NAV_ID_WHITELIST
    orphan_map = (section_map_keys - section_ids) - NAV_ID_WHITELIST

    if not (missing_in_nav or missing_in_map or orphan_nav or orphan_map):
        print(f"audit_lite_nav_section_parity: OK — {len(section_ids)} Lite sections wired to nav + map")
        return 0

    print("audit_lite_nav_section_parity: FAIL")
    if missing_in_nav:
        print(f"  Lite sections without NAV_ITEMS_LITE entry: {sorted(missing_in_nav)}")
        print("    → Add an entry to NAV_ITEMS_LITE in Sidebar.tsx with matching id")
    if missing_in_map:
        print(f"  Lite sections without SECTION_TO_NAV key: {sorted(missing_in_map)}")
        print("    → Add `\"<id>\": \"<id>\",` to SECTION_TO_NAV in Sidebar.tsx")
    if orphan_nav:
        print(f"  NAV_ITEMS_LITE entries without matching Lite section: {sorted(orphan_nav)}")
        print("    → Either add the section anchor to page.tsx or remove the nav entry")
    if orphan_map:
        print(f"  SECTION_TO_NAV keys without matching Lite section: {sorted(orphan_map)}")
        print("    → Remove the orphan key or wire the missing section")
    return 1


if __name__ == "__main__":
    sys.exit(main())
