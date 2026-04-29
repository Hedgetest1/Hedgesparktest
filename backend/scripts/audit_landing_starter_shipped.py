#!/usr/bin/env python3
"""audit_landing_starter_shipped.py — block landing-Lite promises
that don't map to a shipped dashboard component.

(Filename retained for git history; canonical tier name is now
`Lite` per founder directive 2026-04-20. The landing's `key: "lite"`
tier is the one this audit walks.)

Problem class: the landing page's Lite card lists features the
merchant is supposed to access at the entry tier. If we add a bullet
to the landing but never wire the corresponding dashboard surface,
the landing lies. Phase 1.7 caught this manually; this audit catches
it at commit time.

Approach:
- Parse `dashboard/src/app/page.tsx` for the Lite tier's `features`
  array (the tier object with `key: "lite"`) — each string is a
  landing promise.
- For each bullet, verify it matches AT LEAST ONE of:
    a) a known shipped dashboard component (by keyword)
    b) a landing-baseline capability (tracker + basic analytics)
- Bullets with no match are flagged as landing lies.

Coverage claim (honest):
- Catches new Lite bullets that aren't wired to a dashboard
  component — the exact class of drift Phase 1.7 caught manually.
- Does NOT verify the component is ACCESSIBLE to a Lite merchant
  (that requires running the tier-gate logic, not static analysis).
  Sibling `audit_dashboard_fetches.py` covers fetch gate patterns.

Mappings live in BULLET_TO_COMPONENT_KEYWORDS below. Update this
dict whenever a new Lite bullet ships alongside its component.

Exit codes:
    0  clean
    1  unmapped bullet detected
    2  script error
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LANDING_PATH = REPO_ROOT / "dashboard" / "src" / "app" / "page.tsx"
DASHBOARD_COMPONENTS = REPO_ROOT / "dashboard" / "src" / "app" / "components"
DASHBOARD_PULSE = REPO_ROOT / "dashboard" / "src" / "app" / "app" / "page.tsx"

# Bullet string (case-insensitive substring) → one or more component
# name / identifier that MUST exist in the dashboard. Order matters
# only for readability. If a bullet has multiple matching substrings
# (e.g. a combined "Hot Products + Live Radar"), any one hit is enough.
BULLET_TO_COMPONENT_KEYWORDS: dict[str, list[str]] = {
    "first-party pixel tracker": ["tracker.js", "spark-tracker"],
    "visitor intent scoring": ["VisitorIntentCard", "visitor-intent-classification"],
    "revenue-at-risk score": ["RevenueAtRiskHero", "revenue-at-risk"],
    "revenue at risk score": ["RevenueAtRiskHero", "LiteRarsHero"],
    "hot products": ["topProducts", "LiveRadarMap", "intent/products"],
    "live radar": ["LiveRadarMap"],
    "abandoned intent": ["AbandonedIntentCard"],
    "live opportunities": ["LiveOpportunitiesCard", "live-opportunities"],
    "daily intelligence brief": ["BriefHero", "/brief/today"],
    # Lite strategic close 2026-04-29 (commit fe278d5) — full $0-60
    # parity capabilities. Each key is a unique substring of the
    # corresponding tier-card bullet; at least one needle must
    # appear in the dashboard scan blob (components/ + Pulse + layout).
    "p&l · attribution": ["PnlReport", "ChannelAttributionCard"],
    "multi-store consolidation": ["multi-store", "/app/groups"],
    "11-segment rfm": ["RfmSegmentsTile"],
    "custom reports": ["ReportBuilderForm", "/app/reports"],
    "sparkchat": ["AskHedgeSparkCard"],
    "cac : ltv": ["UnitEconomicsCard", "/analytics/cac-ltv"],
    # Landing baseline capabilities that are backend-rendered (not a
    # discrete component) — we still want them listed so they don't
    # look like orphans, but the match target is a known backend path.
    "everything in lite": ["Everything in Lite"],
    "everything in pro": ["Everything in Pro"],
}


def extract_starter_features(landing_text: str) -> list[str]:
    """Return the list of Lite tier bullets from landing page.tsx.

    The source declares `features: [...]` inside a tier object whose
    `key: "lite"`. We scan from `key: "lite"` forward to the next `]`
    that closes the features array. (Function name retained for
    backward compat; the tier was renamed from "Starter" to "Lite"
    on 2026-04-20 per founder directive.)"""
    starter_match = re.search(
        r'key:\s*"lite".*?features:\s*\[(.*?)\]',
        landing_text,
        re.DOTALL,
    )
    if not starter_match:
        return []
    raw = starter_match.group(1)
    return [m.group(1) for m in re.finditer(r'"([^"]+)"', raw)]


DASHBOARD_LAYOUT = REPO_ROOT / "dashboard" / "src" / "app" / "layout.tsx"


def collect_dashboard_tokens() -> str:
    """Return a single blob of dashboard text the audit greps for
    keyword matches. Includes:
    - Pulse page (`/app/page.tsx`)
    - every React component in `components/`
    - root `layout.tsx` (where cross-cutting scripts like tracker.js
      are loaded)"""
    chunks: list[str] = []
    for path in (DASHBOARD_PULSE, DASHBOARD_LAYOUT):
        if path.exists():
            try:
                chunks.append(path.read_text())
            except (OSError, UnicodeDecodeError):
                pass
    if DASHBOARD_COMPONENTS.exists():
        for p in DASHBOARD_COMPONENTS.rglob("*.tsx"):
            try:
                chunks.append(p.read_text())
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(chunks)


def bullet_is_wired(bullet: str, dashboard_blob: str) -> tuple[bool, str]:
    """True iff at least one keyword from the bullet's mapping
    appears in the dashboard blob. Returns (found, reason)."""
    bullet_lower = bullet.lower()
    # Find all mappings whose key is a substring of the bullet
    matching_keys = [
        k for k in BULLET_TO_COMPONENT_KEYWORDS
        if k in bullet_lower
    ]
    if not matching_keys:
        return False, "no keyword mapping (add to BULLET_TO_COMPONENT_KEYWORDS)"
    for k in matching_keys:
        for needle in BULLET_TO_COMPONENT_KEYWORDS[k]:
            if needle in dashboard_blob:
                return True, f"matched '{needle}'"
    return False, f"mapping for '{matching_keys[0]}' does not match dashboard"


@telemetered("audit_landing_starter_shipped")
def main(argv: list[str]) -> int:
    if not LANDING_PATH.exists():
        print(
            f"audit_landing_starter_shipped: {LANDING_PATH} not found",
            file=sys.stderr,
        )
        return 2

    landing_text = LANDING_PATH.read_text()
    bullets = extract_starter_features(landing_text)
    if not bullets:
        print(
            "audit_landing_starter_shipped: no Lite features "
            "array found in landing page.tsx",
            file=sys.stderr,
        )
        return 2

    dashboard_blob = collect_dashboard_tokens()
    unmapped: list[tuple[str, str]] = []

    for bullet in bullets:
        ok, reason = bullet_is_wired(bullet, dashboard_blob)
        if not ok:
            unmapped.append((bullet, reason))

    if not unmapped:
        print(
            f"audit_landing_starter_shipped: clean — all "
            f"{len(bullets)} Lite bullets map to a shipped "
            "dashboard component."
        )
        return 0

    print(
        f"audit_landing_starter_shipped: {len(unmapped)} Starter "
        f"bullet(s) not wired to the dashboard (of {len(bullets)} total)"
    )
    print()
    print("Each bullet below is on the landing Starter card but the")
    print("corresponding dashboard component cannot be located.")
    print("Either ship the component, remove the bullet, or extend the")
    print("BULLET_TO_COMPONENT_KEYWORDS mapping if this is a new pattern.")
    print()
    for bullet, reason in unmapped:
        print(f'  "{bullet}"  →  {reason}')
    print()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_landing_starter_shipped: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
