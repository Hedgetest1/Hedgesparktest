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

import argparse
import re
import sys
from pathlib import Path

# Default Heroicons-outline path snippets per id-prefix. The auto-fix
# inserts a generic clock icon when the missing id has no specific
# match here — never blocks, never invents a domain icon. Founder can
# re-skin manually after auto-fix lands. Intentionally minimal — this
# is auto-repair, not design.
DEFAULT_ICON_PATH = "M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"  # clock outline

ICON_HINTS = {
    "rars":        "M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z",
    "today":       "M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z",
    "last7":       "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5",
    "peers":       "M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z",
    "pnl":         "M2.25 18L9 11.25l4.306 4.306a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941",
    "attribution": "M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244",
    "retention":   "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99",
    "refunds":     "M9 15L3 9m0 0l6-6M3 9h12a6 6 0 010 12h-3",
    "audience":    "M15.182 16.318A4.486 4.486 0 0012.016 15a4.486 4.486 0 00-3.198 1.318M21 12a9 9 0 11-18 0 9 9 0 0118 0zM9.75 9.75c0 .414-.168.75-.375.75S9 10.164 9 9.75 9.168 9 9.375 9s.375.336.375.75zm-.375 0h.008v.015h-.008V9.75zm5.625 0c0 .414-.168.75-.375.75s-.375-.336-.375-.75.168-.75.375-.75.375.336.375.75zm-.375 0h.008v.015h-.008V9.75z",
    "signals":     "M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5",
}

# Section scroll order on /app/lite — used to insert auto-repaired
# NAV_ITEMS_LITE entries at the right slot so the sidebar order stays
# coherent with the rendered scroll order.
SCROLL_ORDER = [
    "lite-rars", "lite-today", "lite-last7",
    "lite-peers", "lite-pnl", "lite-attribution",
    "lite-retention", "lite-refunds", "lite-audience",
    "lite-signals",
]

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
    # Prefer SECTION_TO_NAV_LITE (per-floor map shipped 2026-04-30) —
    # fall back to legacy SECTION_TO_NAV alias if absent.
    section_map_start = src.find("const SECTION_TO_NAV_LITE")
    if section_map_start == -1:
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


def humanize_id(nav_id: str) -> str:
    """Convert `lite-refunds` → `Refunds`, `lite-last7` → `Last 7 days`.
    Fallback table for known ids; otherwise title-case the suffix.
    """
    table = {
        "lite-rars":        "Revenue at risk",
        "lite-today":       "Today",
        "lite-last7":       "Last 7 days",
        "lite-peers":       "You vs peers",
        "lite-pnl":         "Profit",
        "lite-attribution": "Attribution",
        "lite-retention":   "Retention",
        "lite-refunds":     "Refunds",
        "lite-audience":    "Audience",
        "lite-signals":     "Signals",
    }
    if nav_id in table:
        return table[nav_id]
    suffix = nav_id.removeprefix("lite-")
    return suffix.replace("-", " ").title()


def build_nav_item(nav_id: str) -> str:
    """Render a NAV_ITEMS_LITE entry block matching the existing style."""
    label = humanize_id(nav_id)
    suffix = nav_id.removeprefix("lite-")
    icon_path = ICON_HINTS.get(suffix, DEFAULT_ICON_PATH)
    return f'''  {{
    id: "{nav_id}",
    label: "{label}",
    icon: (
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={{1.5}} stroke="currentColor" className="h-4 w-4">
        <path strokeLinecap="round" strokeLinejoin="round" d="{icon_path}" />
      </svg>
    ),
  }},'''


def auto_fix(missing_in_nav: set[str], missing_in_map: set[str]) -> int:
    """Apply deterministic fixes to Sidebar.tsx for missing entries.
    Returns 0 if all fixes applied successfully, non-zero otherwise.
    """
    src = SIDEBAR_TSX.read_text()
    changed = False

    # ── Fix NAV_ITEMS_LITE: insert missing entries at scroll-order slot ──
    if missing_in_nav:
        # Find the closing `];` of NAV_ITEMS_LITE
        nav_start = src.find("const NAV_ITEMS_LITE")
        if nav_start == -1:
            print("auto-fix: cannot locate NAV_ITEMS_LITE — abort", file=sys.stderr)
            return 2
        nav_end = src.find("\n];", nav_start)
        if nav_end == -1:
            print("auto-fix: cannot locate NAV_ITEMS_LITE close — abort", file=sys.stderr)
            return 2

        nav_block = src[nav_start:nav_end]
        # Build the existing id → block-position map by parsing entries
        existing_positions: dict[str, int] = {}
        for m in re.finditer(r'\n  \{\s*\n\s*id:\s*"(lite-[a-z0-9]+|live)"', nav_block):
            existing_positions[m.group(1)] = m.start()

        # Build new entries block
        # Insert each missing id at the correct scroll-order position
        for nav_id in sorted(missing_in_nav, key=lambda x: SCROLL_ORDER.index(x) if x in SCROLL_ORDER else 999):
            entry = build_nav_item(nav_id)
            # Find insertion anchor: the next id in SCROLL_ORDER that already exists
            order_idx = SCROLL_ORDER.index(nav_id) if nav_id in SCROLL_ORDER else len(SCROLL_ORDER)
            anchor_id = None
            for after_id in SCROLL_ORDER[order_idx + 1:]:
                if after_id in existing_positions or after_id in missing_in_nav:
                    if after_id in existing_positions:
                        anchor_id = after_id
                        break
            # Compute insertion position in the live src
            if anchor_id:
                # Insert before anchor_id's `{` line
                anchor_re = re.compile(rf'(\n  \{{\s*\n\s*id:\s*"{re.escape(anchor_id)}")')
                m = anchor_re.search(src)
                if m:
                    insert_at = m.start() + 1  # before the leading \n
                    src = src[:insert_at] + entry + "\n" + src[insert_at:]
                    changed = True
                    existing_positions[nav_id] = insert_at
                    continue
            # Fallback: insert before NAV_ITEMS_LITE close `];`
            nav_end_now = src.find("\n];", src.find("const NAV_ITEMS_LITE"))
            src = src[:nav_end_now] + "\n" + entry + src[nav_end_now:]
            changed = True
            existing_positions[nav_id] = nav_end_now

    # ── Fix SECTION_TO_NAV: append `"<id>": "<id>",` lines ──
    if missing_in_map:
        map_start = src.find("const SECTION_TO_NAV")
        map_end = src.find("};", map_start)
        if map_start == -1 or map_end == -1:
            print("auto-fix: cannot locate SECTION_TO_NAV — abort", file=sys.stderr)
            return 2
        # Insert new keys before the closing `};`. Indent to match existing
        # 2-space indentation inside the map.
        new_keys = "".join(f'  "{k}": "{k}",\n' for k in sorted(missing_in_map))
        # Find the last newline before `};`
        insert_at = src.rfind("\n", map_start, map_end) + 1
        src = src[:insert_at] + new_keys + src[insert_at:]
        changed = True

    if changed:
        SIDEBAR_TSX.write_text(src)
        print(f"auto-fix: NAV_ITEMS_LITE +{len(missing_in_nav)}, SECTION_TO_NAV +{len(missing_in_map)}")
        return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Auto-repair missing entries deterministically")
    args = parser.parse_args()

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

    if args.fix:
        # Self-fix mode: deterministic mechanical repair. Only handles
        # missing-entry cases (additive). Orphan entries require human
        # judgment (delete vs add the missing section) — flagged but
        # not auto-removed.
        if orphan_nav or orphan_map:
            print("audit_lite_nav_section_parity --fix: orphan entries require human review, not auto-removing")
            print(f"  orphan NAV_ITEMS_LITE: {sorted(orphan_nav)}")
            print(f"  orphan SECTION_TO_NAV: {sorted(orphan_map)}")
        if missing_in_nav or missing_in_map:
            return auto_fix(missing_in_nav, missing_in_map)
        return 1

    print("audit_lite_nav_section_parity: FAIL")
    if missing_in_nav:
        print(f"  Lite sections without NAV_ITEMS_LITE entry: {sorted(missing_in_nav)}")
        print("    → Add an entry to NAV_ITEMS_LITE in Sidebar.tsx with matching id")
        print("    → OR run `python scripts/audit_lite_nav_section_parity.py --fix` for auto-repair")
    if missing_in_map:
        print(f"  Lite sections without SECTION_TO_NAV key: {sorted(missing_in_map)}")
        print("    → Add `\"<id>\": \"<id>\",` to SECTION_TO_NAV in Sidebar.tsx")
        print("    → OR run `python scripts/audit_lite_nav_section_parity.py --fix` for auto-repair")
    if orphan_nav:
        print(f"  NAV_ITEMS_LITE entries without matching Lite section: {sorted(orphan_nav)}")
        print("    → Either add the section anchor to page.tsx or remove the nav entry")
    if orphan_map:
        print(f"  SECTION_TO_NAV keys without matching Lite section: {sorted(orphan_map)}")
        print("    → Remove the orphan key or wire the missing section")
    return 1


if __name__ == "__main__":
    sys.exit(main())
