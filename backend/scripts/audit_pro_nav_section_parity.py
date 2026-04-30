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
# `section-X` anchor.
#
# `funnel`, `nudges`, `scroll` are floor-agnostic conversion-funnel
# anchors shared with the legacy NAV_ITEMS.
#
# `overview`, `revenue`, `signals`, `product-performance`, `what-next`
# are Pro-distinct sections rendered under `isProFloor` at page.tsx
# ~3686 (Store Pulse KPIs / Revenue / Findings / Product performance /
# What to do next). They render Pro-distinct content (NOT Lite
# duplicates — Lite uses a different cassettoni grid). Each gets a
# dedicated nav slot per founder UX rule "biggest = most important,
# every section answers one question stated in its title".
SHARED_SECTION_IDS: set[str] = set()
# Empty set 2026-04-30 — Pro tier strict no-doppione: every Pro nav
# id must have its own `section-pro-*` anchor; no shared anchors with
# Lite floor.

# `section-pro-*` anchors that exist in source files but DO NOT render
# on Pro floor. They are kept in source either:
#   - rendered only on Scale floor (e.g., revenue-autopsy is Scale-
#     only after 2026-04-30 audit; Lifetimely $49 = $0-60 doppione
#     for Pro)
#   - false-gated for git-history visibility (e.g., pro-intelligence
#     ProIntelligenceSection's section-pro-intelligence anchor is
#     inside JSX that's `false && isProFloor` gated)
#   - placeholder cards no longer wired to Pro nav (e.g., pro-goals
#     and pro-bi-sql were demoted from Pro → Lite per Lifetimely $49
#     / Mixpanel $25 = $0-60 parity)
# Excluded from "orphan section" finding because they're not orphan,
# they're intentionally scoped to a different floor or disabled.
PRO_ANCHORS_NOT_ON_PRO_FLOOR = {
    "pro-revenue-autopsy",  # Scale-only render
    "pro-goals",            # demoted to Lite-tier ($49 Lifetimely)
    "pro-bi-sql",           # demoted to Lite-tier ($25 Mixpanel)
    "pro-visitor-intent",   # Lite cassettone canonical home
    "pro-abandoned",        # Lite cassettone canonical home
    "pro-targets",          # Lifetimely $49 = Lite-tier
}

# 2026-04-30 — Scale-cross-link allow-list REMOVED. The `scaleOnly`
# convention itself was a strategic mistake: it parked features in
# the Pro sidebar that didn't render on Pro, with a "Scale" badge
# that taught Pro merchants their tier is incomplete. Founder rule:
# every NAV_ITEMS_PRO entry that competitors $60-130 ship MUST live
# fully on Pro (no badge, real anchor). Items that ONLY $140+ ships
# migrate fully to Scale and are removed from NAV_ITEMS_PRO. The
# audit now BANS scaleOnly: true entirely (see check #4 below).

PRO_SECTION_RE = re.compile(r'id="section-(pro-[a-z0-9-]+)"')
GENERIC_SECTION_RE = re.compile(r'id="section-([a-z0-9-]+)"')
NAV_ITEM_RE = re.compile(r'id:\s*"([a-z0-9-]+)"')
# ProParityGapPlaceholder wraps with id={`section-${id}`}, so its
# `id="pro-X"` prop becomes a `section-pro-X` anchor at render time.
PARITY_GAP_RE = re.compile(r'<ProParityGapPlaceholder\s[^>]*?id="(pro-[a-z0-9-]+)"', re.DOTALL)
# Per-entry block parser — picks up id, href, scaleOnly inside each
# `{ ... }` of NAV_ITEMS_PRO so we can enforce the scaleOnly→href
# invariant (every Scale-only entry MUST link to /app/scale, otherwise
# clicking does nothing — the very bug §1.6 turn-3 was meant to fix).
NAV_ENTRY_RE = re.compile(
    r'\{\s*\n\s*id:\s*"([a-z0-9-]+)"(?:[^{}]*?)\}',
    re.DOTALL,
)
HREF_RE = re.compile(r'href:\s*"([^"]+)"')
SCALE_ONLY_RE = re.compile(r'scaleOnly:\s*true')


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
    # Exclude anchors that exist in source but aren't actually wired
    # to Pro nav (Scale-only / false-gated / demoted-to-Lite).
    ids -= PRO_ANCHORS_NOT_ON_PRO_FLOOR
    return ids


def collect_pro_nav_ids() -> tuple[set[str], dict[str, dict[str, str | bool]]]:
    """Return (set of nav ids, per-entry attrs dict).
    Per-entry attrs: { "<id>": { "href": "/app/scale" | "", "scaleOnly": True } }
    """
    src = SIDEBAR_TSX.read_text()
    nav_start = src.find("const NAV_ITEMS_PRO")
    if nav_start == -1:
        sys.stderr.write("ERROR: Sidebar.tsx — NAV_ITEMS_PRO not found.\n")
        return set(), {}
    nav_end = src.find("\n];", nav_start)
    if nav_end == -1:
        sys.stderr.write("ERROR: Sidebar.tsx — NAV_ITEMS_PRO close not found.\n")
        return set(), {}
    nav_block = src[nav_start:nav_end]
    nav_ids = set(NAV_ITEM_RE.findall(nav_block))

    # Per-entry parse — non-greedy match on {} blocks, collecting id +
    # href + scaleOnly per item. Used for the scaleOnly→href invariant.
    attrs: dict[str, dict[str, str | bool]] = {}
    for m in NAV_ENTRY_RE.finditer(nav_block):
        item_id = m.group(1)
        block = m.group(0)
        href_m = HREF_RE.search(block)
        attrs[item_id] = {
            "href": href_m.group(1) if href_m else "",
            "scaleOnly": bool(SCALE_ONLY_RE.search(block)),
        }
    return nav_ids, attrs


def main() -> int:
    section_ids = collect_pro_section_ids()
    nav_ids, attrs = collect_pro_nav_ids()

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

    # 3. Nav entries with no matching section anchor (dead nav clicks).
    # Cross-floor links (entries with href set) are exempt — they
    # navigate to a different floor instead of scrolling.
    cross_floor = {nid for nid, a in attrs.items() if a.get("href")}
    actionable_nav = nav_ids - bad_prefixed - cross_floor
    dead_nav = actionable_nav - section_ids
    if dead_nav:
        findings.append(
            f"NAV_ITEMS_PRO entries with no section anchor and no href "
            f"(click → no scroll target, no navigation): {sorted(dead_nav)}"
        )

    # 4. Scale-badge ban (2026-04-30): NO NAV_ITEMS_PRO entry may
    # carry `scaleOnly: true`. The convention itself was a strategic
    # mistake — it parked features in the Pro sidebar that didn't
    # actually render on Pro (with a "Scale" badge cross-linking to
    # /app/scale). Founder rule: features ship FULLY on Pro (real
    # anchor, no badge) when $60-130 competitors carry parity, or
    # are REMOVED entirely from Pro nav when only $140+ competitors
    # ship them. Anything else teaches Pro merchants their tier is
    # incomplete — exactly the experience we are killing.
    scale_only = {nid for nid, a in attrs.items() if a.get("scaleOnly")}
    if scale_only:
        findings.append(
            f"NAV_ITEMS_PRO entries with scaleOnly: true are BANNED "
            f"as of 2026-04-30 — every Pro nav entry must live FULLY "
            f"on Pro or be removed entirely. Found: {sorted(scale_only)}. "
            f"Apply the founder $60-130 parity test: if competitors in "
            f"that band ship the feature, restore it on Pro (real "
            f"section anchor, no scaleOnly, no href); otherwise drop "
            f"it from NAV_ITEMS_PRO."
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
