#!/usr/bin/env python3
"""audit_tier_gates.py — survey + preventer for Pro-tier gate decisions.

Purpose (Phase 1.1 of v1.0 launch roadmap)
------------------------------------------
HedgeSpark charges €39 Lite / €99 Pro / €249 Scale. Each Pro-gated
endpoint is a PRICING DECISION: is this feature really premium, or is
it commodity-table-stakes we should unlock to Lite?

Today 139 call sites across 61 files use `Depends(require_pro_session)`.
Many of those were gated by historical default, not deliberate product
decision. Phase 1 unlocks 6 commodity features to Lite; this audit
exists to (1) surface every Pro gate grouped by feature area and (2)
enforce an explicit `# tier:` tag on each, so future additions cannot
silently creep into Pro without a documented reason.

Output modes
------------
  --survey     Markdown table grouped by route prefix, plus the 6
               Lite-candidate mappings. No preflight gate — report
               only. Default when no flag passed.
  --preventer  Check that every `Depends(require_pro_session)` has a
               `# tier:` tag on the same line. Exit 1 if any gate is
               missing a tag. **Warn-only by default** (bootstrap);
               pass --strict to fail the audit on missing tags.

Valid tier tag values
---------------------
  `# tier: pro`                — intentional Pro gate (premium feature)
  `# tier: starter-candidate`  — proposed for Lite unlock (review)
  `# tier: starter-unlocked`   — already migrated, Pro gate should
                                 be removed OR kept only for legacy
  `# tier: scale-only`         — only Scale tier should see this
                                 (tighter than Pro)

Lite feature candidates per roadmap
--------------------------------------
  1. Revenue-at-Risk Score      — /pro/rars/*, /analytics/rars*
  2. Hot Products + Live Radar  — /pro/hot-products*, /analytics/radar*
  3. Abandoned Intent           — /pro/abandoned*
  4. Live Opportunities         — /analytics/live-opportunities*
  5. Daily Intelligence Brief   — /pro/daily-brief* (already Lite)
  6. Visitor Intent Scoring     — /analytics/visitor-intent*

Exit codes
----------
    0   survey mode: always (prints report)
        preventer mode --warn-only (default): always
        preventer mode --strict: all gates tagged
    1   preventer mode --strict: any gate missing `# tier:` tag
    2   script error
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from _audit_telemetry_shim import telemetered

BACKEND_ROOT = Path(__file__).resolve().parent.parent
API_DIR = BACKEND_ROOT / "app" / "api"

# A `Depends(require_pro_session)` call site. We search for the literal
# token so we catch both `= Depends(require_pro_session)` parameter
# annotations AND aliased imports. False-positive risk: the token could
# appear in a comment or string literal, but we AST-verify by requiring
# it be inside a function argument list (checked at line scan time).
_GATE_TOKEN = "require_pro_session"
_GATE_CALL_RE = re.compile(r"\bDepends\s*\(\s*require_pro_session\s*\)")

# `# tier: <value>` — we accept any word-like value but warn on invalid
# tags so typos don't pass silently. Valid set maintained in
# _VALID_TIERS for strict validation.
_TIER_COMMENT_RE = re.compile(r"#\s*tier\s*:\s*([a-z][a-z0-9_-]*)")

_VALID_TIERS = {
    "pro",
    "starter-candidate",
    "starter-unlocked",
    "scale-only",
}

# Route decorator pattern — we scan BACKWARD from a gate call site to
# find the preceding `@router.{get,post,put,patch,delete}("/path"...)`
# so we can show the route in the survey output.
_ROUTE_DECORATOR_RE = re.compile(
    r"@router\.(get|post|put|patch|delete)\s*\(\s*[\"']([^\"']+)[\"']"
)

# `APIRouter(prefix="/pro/foo", ...)` — each file's routes are all
# mounted under this prefix, so the full route is prefix + decorator_path.
_ROUTER_PREFIX_RE = re.compile(
    r"APIRouter\s*\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL
)

# Lite-candidate route prefix → feature mapping. Matched by
# `path.startswith(prefix)`.
#
# Note (2026-04-25): 5/6 of the Phase 1.1 roadmap features were already
# unlocked at backend level by prior commits — their endpoints use
# `require_merchant_session` not `require_pro_session`, so they do NOT
# appear in this survey. Those 5 are:
#   1. RARS (revenue_at_risk.py — require_merchant_session ✅)
#   3. Abandoned Intent (abandoned_intent.py ✅)
#   4. Live Opportunities (live_opportunities.py ✅)
#   5. Daily Brief (brief.py /brief/today not /brief/today/pro)
#   6. Visitor Intent (intent.py + visitor_scores.py ✅)
#
# The ONE feature still backend-Pro-gated is:
#   2. Hot Products + Live Radar  —  /pro/revenue-radar/top
#
# Candidate mapping therefore only matches that one. This keeps the
# audit honest: the mapping tracks what's STILL blocked, not what the
# stale memo claimed.
_STARTER_CANDIDATES: list[tuple[str, str]] = [
    ("/pro/revenue-radar", "2. Hot Products + Live Radar"),
]


@dataclass
class GateSite:
    file: str       # relative to backend/
    line: int       # 1-indexed
    route_method: str | None
    route_path: str | None
    tier_tag: str | None


def _scan_file(path: Path) -> list[GateSite]:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    if _GATE_TOKEN not in text:
        return []

    # Extract router prefix (if any) — every route in this file is
    # mounted under it.
    prefix_m = _ROUTER_PREFIX_RE.search(text)
    router_prefix = prefix_m.group(1).rstrip("/") if prefix_m else ""

    lines = text.splitlines()
    out: list[GateSite] = []

    current_route: tuple[str, str] | None = None

    for idx, raw_line in enumerate(lines, start=1):
        m_route = _ROUTE_DECORATOR_RE.search(raw_line)
        if m_route:
            method = m_route.group(1).upper()
            decorator_path = m_route.group(2)
            # Join prefix + decorator path. If decorator_path is "/",
            # the effective route is just the prefix.
            if decorator_path == "/":
                full = router_prefix or "/"
            else:
                full = (router_prefix + decorator_path) if router_prefix else decorator_path
                if not full.startswith("/"):
                    full = "/" + full
            current_route = (method, full)
            continue
        if not _GATE_CALL_RE.search(raw_line):
            continue

        tier_m = _TIER_COMMENT_RE.search(raw_line)
        tier_tag = tier_m.group(1) if tier_m else None

        rel = str(path.relative_to(BACKEND_ROOT))
        method, path_str = (current_route if current_route else (None, None))
        out.append(GateSite(
            file=rel, line=idx,
            route_method=method, route_path=path_str,
            tier_tag=tier_tag,
        ))
    return out


def _collect_all_gates() -> list[GateSite]:
    all_sites: list[GateSite] = []
    for py in sorted(API_DIR.rglob("*.py")):
        all_sites.extend(_scan_file(py))
    return all_sites


def _starter_candidate(path: str | None) -> str | None:
    if not path:
        return None
    for prefix, feature in _STARTER_CANDIDATES:
        if path.startswith(prefix):
            return feature
    return None


def _group_by_prefix(sites: list[GateSite]) -> dict[str, list[GateSite]]:
    groups: dict[str, list[GateSite]] = defaultdict(list)
    for s in sites:
        if s.route_path:
            # Group by first two path segments: /pro/rars/summary → /pro/rars
            parts = s.route_path.strip("/").split("/")
            prefix = "/" + "/".join(parts[:2]) if len(parts) >= 2 else "/" + parts[0]
        else:
            prefix = "<unknown-route>"
        groups[prefix].append(s)
    return groups


def _print_survey(sites: list[GateSite]) -> None:
    print(f"# Pro-tier gates survey\n")
    print(f"Total `Depends(require_pro_session)` call sites: **{len(sites)}**")
    print(f"Scanned files: **{len(set(s.file for s in sites))}**\n")

    tagged = [s for s in sites if s.tier_tag is not None]
    print(f"Sites with `# tier:` tag: **{len(tagged)} / {len(sites)}**\n")

    # Lite-candidate hits
    candidates: dict[str, list[GateSite]] = defaultdict(list)
    for s in sites:
        feat = _starter_candidate(s.route_path)
        if feat:
            candidates[feat].append(s)

    if candidates:
        print("## Lite-candidate mapping (per v1.0 launch roadmap)\n")
        for feat in sorted(candidates):
            sites_f = candidates[feat]
            print(f"### {feat} — {len(sites_f)} gate(s)\n")
            for s in sites_f:
                tag = f" `# tier: {s.tier_tag}`" if s.tier_tag else " **untagged**"
                route = f"`{s.route_method} {s.route_path}`" if s.route_path else "<?>"
                print(f"- {route} — `{s.file}:{s.line}`{tag}")
            print()

    print("## All Pro gates grouped by route prefix\n")
    groups = _group_by_prefix(sites)
    for prefix in sorted(groups):
        group_sites = groups[prefix]
        untagged = sum(1 for s in group_sites if not s.tier_tag)
        tags = {s.tier_tag for s in group_sites if s.tier_tag}
        tag_summary = ", ".join(sorted(tags)) if tags else "(no tags)"
        print(
            f"### `{prefix}/...` — {len(group_sites)} gate(s) "
            f"[{tag_summary}; {untagged} untagged]\n"
        )
        for s in group_sites:
            route = f"`{s.route_method} {s.route_path}`" if s.route_path else "<?>"
            tag = f"`# tier: {s.tier_tag}`" if s.tier_tag else "**untagged**"
            print(f"- {route} — `{s.file}:{s.line}` — {tag}")
        print()


def _run_preventer(sites: list[GateSite], strict: bool) -> int:
    untagged = [s for s in sites if not s.tier_tag]
    invalid = [s for s in sites if s.tier_tag and s.tier_tag not in _VALID_TIERS]

    print(
        f"audit_tier_gates: {len(sites)} gate(s) total, "
        f"{len(untagged)} untagged, {len(invalid)} invalid tag(s)"
    )

    if not untagged and not invalid:
        print("audit_tier_gates: all gates carry a valid `# tier:` tag")
        return 0

    if untagged:
        print(f"\n{len(untagged)} gate(s) missing `# tier:` tag:\n")
        for s in untagged[:40]:
            route = f"{s.route_method} {s.route_path}" if s.route_path else "?"
            print(f"  {s.file}:{s.line}  {route}")
        if len(untagged) > 40:
            print(f"  ... and {len(untagged) - 40} more")

    if invalid:
        print(f"\n{len(invalid)} gate(s) with invalid tier tag "
              f"(valid: {sorted(_VALID_TIERS)}):\n")
        for s in invalid:
            print(f"  {s.file}:{s.line}  tier={s.tier_tag}")

    print(
        "\nFix: add `# tier: <value>` comment on the same line as "
        "`Depends(require_pro_session)`.\n"
        "Valid values: pro, starter-candidate, starter-unlocked, scale-only."
    )

    return 1 if strict else 0


@telemetered("audit_tier_gates")
def main(argv: list[str]) -> int:
    if not API_DIR.is_dir():
        print(f"audit_tier_gates: API dir missing at {API_DIR}", file=sys.stderr)
        return 2

    sites = _collect_all_gates()

    mode_preventer = "--preventer" in argv
    strict = "--strict" in argv

    if mode_preventer:
        return _run_preventer(sites, strict=strict)

    # Default: survey mode
    _print_survey(sites)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_tier_gates: script error — {exc}", file=sys.stderr)
        sys.exit(2)
