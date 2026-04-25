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
# multi-line decorators. The route path is on the same logical line.
DECORATOR_PATTERN = re.compile(
    r'@(?:app|router)\.(?:get|post|put|delete)\s*\(\s*["\']([^"\']+)["\']',
    re.MULTILINE,
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

        # Walk file: collect decorators with their following def() line
        # to know which auth decorator applies.
        lines = text.split("\n")
        for i, line in enumerate(lines):
            m_dec = DECORATOR_PATTERN.search(line)
            if not m_dec:
                continue
            route = m_dec.group(1)
            # Look ahead up to 30 lines for the function def to read its
            # auth dep. Stop at the next decorator.
            uses_lite_auth = False
            uses_pro_auth = False
            for j in range(i + 1, min(i + 30, len(lines))):
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
            out.append((full, py, line.strip()))
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


def main() -> int:
    endpoints = find_endpoints()
    orphans: list[tuple[str, Path]] = []
    for route, file, _ in endpoints:
        hits = grep_dashboard(route)
        if not hits:
            orphans.append((route, file))
            continue
        if not is_lite_rendered(hits):
            orphans.append((route, file))

    if not orphans:
        print("✅ No Lite-orphan endpoints — every Lite-accessible API has a Lite-rendered consumer.")
        return 0

    print(f"⚠ {len(orphans)} Lite-accessible endpoint(s) with no detected Lite-floor render path:")
    for route, file in sorted(orphans):
        print(f"   {route:50s}  ← {file.relative_to(BACKEND_API.parent.parent)}")
    print()
    print("Decide per endpoint:")
    print("  (a) expose on Lite — wire the existing component into the")
    print("      isLiteFloor branch of dashboard/src/app/app/page.tsx")
    print("  (b) intentional Pro-only render — change auth to")
    print("      require_pro_session OR move under a /pro/ route prefix")
    print()
    print("This audit is informational; preflight does NOT fail on it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
