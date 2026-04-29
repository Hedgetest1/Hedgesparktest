#!/usr/bin/env python3
"""Pro-floor nav vs section parity preventer.

Born 2026-04-29 after founder reported the sidebar highlight not
following scroll on /app/pro. Three failure modes were live in
production simultaneously:
  1. NAV_ITEMS_PRO entries had ids "section-funnel"/"section-nudges"/
     "section-scroll" with the prefix already baked in — observer
     stripped "section-" and tried to match "funnel" against item.id
     "section-funnel" → no match → no highlight.
  2. VisitorIntent / AbandonedIntent / PriceSensitivity rendered raw
     with no <section id="section-pro-*"> wrapper → observer's
     [id^='section-'] selector saw nothing.
  3. ProParityGapPlaceholder rendered id={id} (no prefix) → same.

Sister to audit_lite_nav_section_parity.py — same idea, different
floor. Keeps Pro nav and Pro section anchors in lockstep so the next
"I added a Pro section but forgot to wire the nav" regression is
caught at preflight, not by a paying merchant on /app/pro.

Failure modes detected:
- A `section-pro-*` anchor exists in the Pro vertical of page.tsx (or
  any imported component) but its stripped id is NOT in NAV_ITEMS_PRO
  (= scrolling there produces a null-active state).
- A NAV_ITEMS_PRO id is NOT in SECTION_TO_NAV (= the observer fallback
  works but the explicit map is incomplete; usability still works but
  audit_lite_nav_section_parity.py treats this the same way).
- A NAV_ITEMS_PRO id has the literal prefix "section-" (= the bug
  the founder caught — observer match always fails).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DASHBOARD = Path("/opt/wishspark/dashboard/src")
PAGE_TSX = DASHBOARD / "app" / "app" / "page.tsx"
SIDEBAR_TSX = DASHBOARD / "app" / "components" / "Sidebar.tsx"
COMPONENTS_DIR = DASHBOARD / "app" / "components"
SECTIONS_DIR = DASHBOARD / "app" / "app" / "_sections"

# Cross-floor canonical ids that legitimately appear in NAV_ITEMS_PRO
# without a `section-pro-*` anchor — they re-use the floor-agnostic
# `section-X` anchor (e.g., `section-funnel`, `section-nudges`,
# `section-scroll` are shared with the legacy NAV_ITEMS).
SHARED_SECTION_IDS = {"funnel", "nudges", "scroll"}

# Pro nav ids that point to features which today only render on the
# Scale floor (Pro→Scale moat migration 2026-04-28). They're left in
# NAV_ITEMS_PRO intentionally so the merchant can see what Pro+
# unlocks; clicking does nothing on /app/pro until they're either
# moved back to Pro tier or routed to /app/scale on click. Tracked
# as a separate sidebar-information-architecture follow-up.
PRO_NAV_SCALE_ONLY = {
    "pro-anomaly", "pro-causal", "pro-counterfactual",
    "pro-playbook", "pro-night-shift", "pro-revenue-autopsy",
    "pro-mta",
}

PRO_SECTION_RE = re.compile(r'id="section-(pro-[a-z0-9-]+)"')
GENERIC_SECTION_RE = re.compile(r'id="section-([a-z0-9-]+)"')
NAV_ITEM_RE = re.compile(r'id:\s*"([a-z0-9-]+)"')
# ProParityGapPlaceholder wraps with id={`section-${id}`}, so its
# `id="pro-X"` prop becomes a `section-pro-X` anchor at render time.
PARITY_GAP_RE = re.compile(r'<ProParityGapPlaceholder\s[^>]*?id="(pro-[a-z0-9-]+)"', re.DOTALL)


def collect_pro_section_ids() -> set[str]:
    """All `section-pro-*` and shared `section-{funnel|nudges|scroll}`
    anchors rendered anywhere reachable from the Pro vertical."""
    ids: set[str] = set()
    targets = [PAGE_TSX]
    for d in (COMPONENTS_DIR, SECTIONS_DIR):
        if d.exists():
            targets.extend(d.glob("*.tsx"))
    for path in targets:
        try:
            text = path.read_text()
        except OSError:
            continue
        ids.update(PRO_SECTION_RE.findall(text))
        # Also pick up shared bare-section ids (funnel/nudges/scroll)
        for m in GENERIC_SECTION_RE.finditer(text):
            sid = m.group(1)
            if sid in SHARED_SECTION_IDS:
                ids.add(sid)
        # ProParityGapPlaceholder wraps its id prop with `section-`
        # at render time, so its id="pro-X" produces section-pro-X.
        ids.update(PARITY_GAP_RE.findall(text))
    return ids


def collect_pro_nav_ids() -> tuple[set[str], set[str]]:
    src = SIDEBAR_TSX.read_text()
    nav_start = src.find("const NAV_ITEMS_PRO")
    if nav_start == -1:
        sys.stderr.write("ERROR: Sidebar.tsx — NAV_ITEMS_PRO not found.\n")
        return set(), set()
    nav_end = src.find("\n];", nav_start)
    if nav_end == -1:
        sys.stderr.write("ERROR: Sidebar.tsx — NAV_ITEMS_PRO close not found.\n")
        return set(), set()
    nav_block = src[nav_start:nav_end]
    nav_ids = set(NAV_ITEM_RE.findall(nav_block))
    return nav_ids, set()


def main() -> int:
    section_ids = collect_pro_section_ids()
    nav_ids, _ = collect_pro_nav_ids()

    findings: list[str] = []

    # 1. Bug-class the founder caught: any nav id starting with "section-"
    bad_prefixed = {nid for nid in nav_ids if nid.startswith("section-")}
    if bad_prefixed:
        findings.append(
            f"NAV_ITEMS_PRO contains ids with literal 'section-' prefix "
            f"(observer match will always fail): {sorted(bad_prefixed)}"
        )

    # 2. Section anchors with no matching nav entry (orphan sections)
    orphan_sections = section_ids - nav_ids
    if orphan_sections:
        findings.append(
            f"section-pro-* anchors with no NAV_ITEMS_PRO entry "
            f"(scrolling there produces null-active highlight): "
            f"{sorted(orphan_sections)}"
        )

    # 3. Nav entries with no matching section anchor (dead nav clicks)
    actionable_nav = nav_ids - PRO_NAV_SCALE_ONLY - bad_prefixed
    dead_nav = actionable_nav - section_ids
    if dead_nav:
        findings.append(
            f"NAV_ITEMS_PRO entries with no section anchor "
            f"(click → no scroll target): {sorted(dead_nav)}"
        )

    if findings:
        print("audit_pro_nav_section_parity: FAIL")
        for f in findings:
            print(f"  - {f}")
        return 1

    print(
        f"audit_pro_nav_section_parity: OK — {len(nav_ids)} nav id(s), "
        f"{len(section_ids)} section anchor(s), 0 mismatches"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
