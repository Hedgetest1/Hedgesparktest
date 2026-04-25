#!/usr/bin/env python3
"""
audit_lite_orphan_endpoints — Detect base-analytics drift on /app/lite.

Class of bug this prevents
--------------------------
The 2026-04-25 audit caught a structural gap: several Lite-accessible
endpoints (`/orders/summary`, `/orders/daily-revenue`, `/orders/product-
conversions`, `/analytics/funnel`, `/products/store-intelligence`, etc.)
were exposed in the backend with `require_merchant_session` BUT their
React components were rendered only inside the `!isLiteFloor` branch of
`dashboard/src/app/app/page.tsx`. A merchant on the Lite floor saw zero
trace of those analytics even though the data was fully prepared.

How this audit works
--------------------
1. Walk every `app/api/**/*.py` for `@router.get/post(...)` lines that
   use `require_merchant_session` (NOT `require_pro_session`) — these
   are Lite-eligible endpoints.
2. Skip endpoints whose route mounts under `/pro/` (intentionally
   Pro-only at the URL layer).
3. For each remaining endpoint, search the dashboard source for any
   reference to its route path. Flag endpoints with zero references —
   they're orphan from the Lite UI's point of view.

This is intentionally a WARN-only sweep (exit 0 always). Some
endpoints are intentionally rendered only on Pro floors even when the
backend is open (e.g. RevenueHero is Pro-floor only). The audit
surfaces candidates so a human can decide whether to expose them on
Lite or document the gap.

Usage
-----
    cd /opt/wishspark/backend
    ./venv/bin/python scripts/audit_lite_orphan_endpoints.py

Run before any Lite-floor sprint to catch drift early.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BACKEND_API = Path("/opt/wishspark/backend/app/api")
DASHBOARD_SRC = Path("/opt/wishspark/dashboard/src")

# Routes that mount under /pro/ are intentionally Pro-only at the URL
# layer regardless of the auth decorator — skip them.
PRO_PREFIX_PATTERN = re.compile(r'(^|/)pro/')

# Capture: @router.get("/path", ...) or @router.post("/path"), allowing
# the path argument to be on a continuation line — many endpoints in
# this codebase split the decorator across multiple lines:
#     @router.get(
#         "/sessions",
#         response_model=...
#     )
# `re.DOTALL` makes `.` span newlines so the regex can reach the path.
DECORATOR_PATTERN = re.compile(
    r'@(?:app|router)\.(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']',
    re.DOTALL,
)


def find_endpoints() -> list[tuple[str, Path, str]]:
    """Return [(full_route, file, decorator_line)] for each Lite-eligible
    endpoint. full_route includes the router prefix when present."""
    out: list[tuple[str, Path, str]] = []
    for py in BACKEND_API.rglob("*.py"):
        try:
            text = py.read_text()
        except Exception:
            continue
        # Find router prefix: APIRouter(prefix="/orders", ...)
        m = re.search(r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']', text)
        prefix = m.group(1) if m else ""

        # Walk decorators in the file regardless of line boundaries —
        # match the whole-file content so multi-line decorators are
        # captured. For each match, extract the surrounding window
        # (decorator end → next 30 lines) to read the auth dep.
        lines = text.split("\n")
        for m_dec in DECORATOR_PATTERN.finditer(text):
            route = m_dec.group(1)
            # Locate the line index where the decorator's path token sits
            # so we can walk forward to the function def.
            decorator_line_idx = text[:m_dec.end()].count("\n")
            uses_lite_auth = False
            uses_pro_auth = False
            for j in range(decorator_line_idx + 1, min(decorator_line_idx + 30, len(lines))):
                if DECORATOR_PATTERN.search(lines[j]):
                    break
                if "require_pro_session" in lines[j]:
                    uses_pro_auth = True
                    break
                if "require_merchant_session" in lines[j]:
                    uses_lite_auth = True
                    break
            if not uses_lite_auth or uses_pro_auth:
                continue
            full = f"{prefix}{route}" if prefix else route
            if PRO_PREFIX_PATTERN.search(full):
                continue
            decorator_line = lines[decorator_line_idx].strip() if decorator_line_idx < len(lines) else ""
            out.append((full, py, decorator_line))
    return out


def grep_dashboard(route: str) -> list[str]:
    """Return matching paths in dashboard source for a route reference.
    Uses ripgrep when available, falls back to a Python walk."""
    # Quote-aware grep — looks for the route as a substring of any
    # string literal in the dashboard. Many endpoints are referenced via
    # `apiClient.GET("/orders/summary")` or `fetch(\`${apiBase}/orders/summary\`)`.
    hits: list[str] = []
    # Try ripgrep
    try:
        r = subprocess.run(
            ["rg", "-l", "--no-messages", route, str(DASHBOARD_SRC)],
            capture_output=True, text=True, timeout=20,
        )
        hits = [ln for ln in r.stdout.split("\n") if ln.strip()]
    except FileNotFoundError:
        for p in DASHBOARD_SRC.rglob("*"):
            if p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".jsx"}:
                try:
                    if route in p.read_text():
                        hits.append(str(p))
                except Exception:
                    continue
    return hits


def is_lite_rendered(hits: list[str]) -> bool:
    """A reference counts as Lite-rendered if at least one of the hit
    files is itself a Lite-only component (LiteRars*, LiteCassettoni*,
    LiteToday*, LiteVisitorIntent*) OR if a hit appears in a file that
    doesn't gate by !isLiteFloor.

    We approximate by checking each hit file: if its content references
    the route AND does NOT wrap that reference in a !isLiteFloor branch,
    it counts. The cheap approximation: look for any Lite-named
    component file as a hit, OR for a non-page.tsx hit (page.tsx mixes
    both floors)."""
    for h in hits:
        name = Path(h).name
        if name.startswith("Lite"):
            return True
        # Component files that aren't page.tsx and aren't the Pro-named
        # series typically render on both floors.
        if "page.tsx" not in h and "Pro" not in name and "Intelligence" not in name:
            return True
    return False


# ---------------------------------------------------------------------------
# Sub-class B — fetch hidden behind a tier === "pro" gate
# ---------------------------------------------------------------------------
# The 2026-04-25 retro DA caught a subtler variant of the same class:
# the route IS referenced from dashboard/src/app/app/page.tsx (so the
# orphan-endpoint pass thinks it's covered), but the call site is
# wrapped in `if (tier === "pro")` or `if (tier !== "pro") return`,
# meaning Lite merchants never trigger the network call. The consumer
# component then renders empty forever — same theater bug, different
# mechanism. This sub-audit greps the page module for that specific
# pattern.

PAGE_TSX = DASHBOARD_SRC / "app" / "app" / "page.tsx"

# Routes whose consumers are intentionally Pro-only (heavy UX, privacy-
# sensitive replay viewer, click-heatmap). Backend exposes them with
# require_merchant_session for completeness, but the dashboard skips
# the network call on Lite to save a useless round-trip. Any addition
# here MUST link to the consumer component that renders only on Pro.
TIER_GATE_EXEMPT: set[str] = {
    "/analytics/sessions",  # consumer: _sections/SessionsSection.tsx (Pro-only)
    "/analytics/clicks",    # consumer: clicks heatmap (Pro-only)
}

# A simple line-window heuristic: for each route reference inside
# page.tsx, look up to 30 lines above for an opening `if (tier === "pro"`
# or `if (tier !== "pro") return` statement that hasn't been closed.
TIER_GATE_PATTERNS = (
    re.compile(r'\bif\s*\(\s*[^)]*\btier\s*===\s*["\']pro["\']'),
    re.compile(r'\bif\s*\(\s*[^)]*\btier\s*!==\s*["\']pro["\']\s*\)\s*return'),
)


def is_under_tier_pro_gate(route: str) -> bool:
    """Return True iff the only references to `route` inside page.tsx
    are wrapped in a `tier === "pro"` (or `tier !== "pro" return`)
    block. Approximation only — does naive brace-balance counting."""
    if not PAGE_TSX.exists():
        return False
    try:
        text = PAGE_TSX.read_text()
    except Exception:
        return False
    lines = text.split("\n")

    # Find all line indices that look like a RUNTIME call to the route.
    # We deliberately skip type-import lines like `paths["/foo"]` since
    # those are typed schema references, not network calls; treating
    # them as "lite-rendered" would always return False even when the
    # actual call site is gated.
    # Anchor the route boundary so `/attribution/summary` does NOT match
    # `/attribution/summary/pro`. The route must end at the closing
    # quote, a `?` (query string), `/` only at the route's natural
    # trailing-slash, or end-of-string. We accept characters that
    # commonly follow a route in source: quote, `?`, `${` (template
    # interpolation), `,`, ` `, `)`. We also strip a trailing slash
    # from the route before escaping.
    norm_route = route.rstrip("/")
    runtime_call = re.compile(
        r'(apiClient\.(?:GET|POST|PUT|DELETE)|\bfetch\s*\()'
        r'[^)]*?["\'`][^"\'`]*'
        + re.escape(norm_route)
        + r'(?:[?"\'`]|\$\{)'  # boundary: query, end-quote, or interpolation
    )
    ref_lines = [i for i, ln in enumerate(lines) if runtime_call.search(ln)]
    if not ref_lines:
        return False  # not actually called at runtime from page.tsx

    def gated(idx: int) -> bool:
        # Walk backwards up to 60 lines collecting opening braces and
        # tier gates; if a gate-open lies above without a matching close,
        # we consider the line gated.
        brace_balance = 0
        for j in range(idx, max(idx - 60, -1), -1):
            line = lines[j]
            brace_balance += line.count("}") - line.count("{")
            if brace_balance < 0:
                # we're inside an open block above
                for pat in TIER_GATE_PATTERNS:
                    if pat.search(line):
                        return True
        return False

    return all(gated(i) for i in ref_lines)


def main() -> int:
    endpoints = find_endpoints()
    orphans: list[tuple[str, Path]] = []
    tier_gated: list[tuple[str, Path]] = []
    for route, file, _ in endpoints:
        hits = grep_dashboard(route)
        if not hits:
            orphans.append((route, file))
            continue
        if not is_lite_rendered(hits):
            orphans.append((route, file))
            continue
        # Sub-audit B — referenced but only under tier === "pro" gate
        if route in TIER_GATE_EXEMPT:
            continue
        if is_under_tier_pro_gate(route):
            tier_gated.append((route, file))

    if not orphans and not tier_gated:
        print("✅ No Lite-orphan endpoints — every Lite-accessible API has a Lite-rendered consumer not gated to Pro.")
        return 0

    if orphans:
        print(f"⚠ {len(orphans)} Lite-accessible endpoint(s) with no detected Lite-floor render path:")
        for route, file in sorted(orphans):
            print(f"   {route:50s}  ← {file.relative_to(BACKEND_API.parent.parent)}")
        print()

    if tier_gated:
        print(f"⚠ {len(tier_gated)} Lite-accessible endpoint(s) referenced ONLY inside `tier === \"pro\"` gate:")
        for route, file in sorted(tier_gated):
            print(f"   {route:50s}  ← {file.relative_to(BACKEND_API.parent.parent)}")
        print()
        print("These endpoints are Lite-accessible at the backend but the")
        print("frontend never fires the call for Lite users — consumers")
        print("on the Lite floor will render empty forever (theater).")
        print("Move the fetch out of the tier gate or document the intent.")
        print()

    print("Decide per endpoint:")
    print("  (a) expose on Lite — wire the existing component into the")
    print("      isLiteFloor branch + remove tier-gate around the fetch")
    print("  (b) intentional Pro-only render — change auth to")
    print("      require_pro_session OR move under a /pro/ route prefix")
    print()
    print("This audit is informational; preflight does NOT fail on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
