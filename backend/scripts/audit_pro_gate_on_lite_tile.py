#!/usr/bin/env python
"""
audit_pro_gate_on_lite_tile.py — preflight invariant.

Catches the bug class shipped on G6 pre-fix: a frontend component
rendered on the Lite floor (`{isLiteFloor && <Card />}`) that calls
a backend endpoint with `require_pro_session`. Result: Lite users
hit 403 from the API and see error states / blank tiles.

Why it's a bug class
--------------------
Tier-gating is split across 2 layers in HedgeSpark:
  - Backend: `require_pro_session` vs `require_merchant_session`
  - Frontend: `{isLiteFloor && <X />}` (Lite-only) vs `{!isLiteFloor && <X />}`
                (Pro+) vs `{isProUser && !isLiteFloor && <X />}` (Pro+)

A drift bug is when a component intended for Lite calls an endpoint
gated to Pro. The /pro/cac-ltv → UnitEconomicsCard rendering on Lite
floor was the canonical example caught manually 2026-04-29.

What this audits
----------------
Two-pass static analysis:
  1. Build a registry of (endpoint_path → require_*_session) from
     `app/api/*.py` by parsing each `@router.<verb>` + the matching
     `Depends(require_*_session)` argument.
  2. Walk `dashboard/src/app` for `.tsx` files. For each component
     file, find:
       - apiClient.GET/POST/PUT/PATCH/DELETE("/path") calls
       - fetch() calls to /pro|/merchant|/analytics paths
     Then check the SAME file (and the page.tsx that consumes it)
     for an `isLiteFloor` rendering condition. If the endpoint is
     Pro-gated AND the component renders under isLiteFloor → flag.

False-positive guard: components rendered with the explicit
`{isLiteFloor && (isProUser ? <X /> : <ProLockedTile />)}` pattern
are exempt — that's the canonical "preview lock" UX.

Limitations
-----------
- Static-only: doesn't cross component boundaries (props that
  enable/disable renders aren't inferred).
- The scan-page side is heuristic: we look at `/dashboard/src/app/app/page.tsx`
  for the `<Component>` usage and check the surrounding 8 lines for
  isLiteFloor.

Usage
-----
    ./venv/bin/python scripts/audit_pro_gate_on_lite_tile.py
    ./venv/bin/python scripts/audit_pro_gate_on_lite_tile.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from _audit_io import safe_read_text

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
BACKEND_API_DIR = REPO_ROOT / "backend" / "app" / "api"
DASHBOARD_DIR = REPO_ROOT / "dashboard" / "src" / "app"
APP_PAGE = REPO_ROOT / "dashboard" / "src" / "app" / "app" / "page.tsx"


# Step 1 — endpoint → tier-gate map.
# Match decorator + Depends(require_*_session) within ~12 lines below.
_ROUTER_DECO_RE = re.compile(
    r"""@router\.(?:get|post|put|patch|delete)\s*\(\s*["'](?P<path>[^"']+)["']""",
)
_GATE_RE = re.compile(
    r"""Depends\s*\(\s*(?P<gate>require_(?:pro|merchant|scale)_session)\s*\)""",
)


def build_endpoint_gate_map() -> dict[str, str]:
    """Walk app/api and return {path: gate_name}."""
    out: dict[str, str] = {}
    # Common router prefix patterns — extracted from `APIRouter(prefix="...")`.
    _PREFIX_RE = re.compile(r"""APIRouter\s*\(\s*(?:prefix\s*=\s*)?["'](?P<prefix>[^"']+)["']""")
    for py_file in BACKEND_API_DIR.rglob("*.py"):
        text = safe_read_text(py_file)
        if text is None:
            continue
        prefix_match = _PREFIX_RE.search(text)
        prefix = prefix_match.group("prefix") if prefix_match else ""
        for deco in _ROUTER_DECO_RE.finditer(text):
            # Find the next `Depends(require_*_session)` within ~600 chars.
            window = text[deco.end():deco.end() + 600]
            gate_match = _GATE_RE.search(window)
            if not gate_match:
                continue
            full_path = prefix + deco.group("path")
            out[full_path] = gate_match.group("gate")
    return out


# Step 2 — component-level scan.
_API_CALL_RE = re.compile(
    r"""apiClient\.(?:GET|POST|PUT|PATCH|DELETE)\s*\(\s*["'](?P<path>/[^"']+)["']""",
)


@telemetered("audit_pro_gate_on_lite_tile")
def audit() -> int:
    endpoint_gates = build_endpoint_gate_map()
    pro_endpoints = {p for p, g in endpoint_gates.items() if g == "require_pro_session"}

    if not pro_endpoints:
        print("✗ endpoint-gate map empty — audit broken")
        return 1

    # Scan main dashboard page for component → isLiteFloor render context.
    page_text = safe_read_text(APP_PAGE)
    if page_text is None:
        print(f"✗ cannot read {APP_PAGE}")
        return 1

    findings: list[dict] = []
    # For each component file in dashboard/src/app/components, check
    # if it calls a Pro endpoint AND is rendered under isLiteFloor.
    components_dir = DASHBOARD_DIR / "components"
    for tsx in components_dir.rglob("*.tsx"):
        comp_text = safe_read_text(tsx)
        if comp_text is None:
            continue
        # Endpoints called by this component
        called_pro_paths = set()
        for m in _API_CALL_RE.finditer(comp_text):
            path = m.group("path")
            # Strip `{group_id}` placeholders and query strings
            path_norm = re.sub(r"\{[^}]+\}", "{}", path).split("?")[0]
            for ep in pro_endpoints:
                ep_norm = re.sub(r"\{[^}]+\}", "{}", ep).split("?")[0]
                if path_norm == ep_norm or path_norm.startswith(ep_norm.rstrip("/") + "/"):
                    called_pro_paths.add(ep)
        if not called_pro_paths:
            continue
        # Now check page.tsx for `<ComponentName ...>` usage under isLiteFloor.
        comp_name = tsx.stem
        # Find every render of <ComponentName in page.tsx
        usage_re = re.compile(rf"""<{re.escape(comp_name)}\b""")
        for usage in usage_re.finditer(page_text):
            # Look at the 600 chars BEFORE the usage for the rendering condition.
            ctx_start = max(0, usage.start() - 600)
            ctx = page_text[ctx_start:usage.start()]
            # Find the most recent {isLiteFloor && or {!isLiteFloor && or
            # {isProUser && !isLiteFloor && in the context.
            # We want to flag ONLY the {isLiteFloor && case (without isProUser nesting).
            # Strip out 'isProUser && !isLiteFloor &&' patterns first.
            ctx_clean = re.sub(
                r"\{isProUser\s*&&\s*!isLiteFloor\s*&&", "{NOT_LITE_BUT_PRO &&", ctx
            )
            ctx_clean = re.sub(
                r"\{!isLiteFloor\s*&&", "{NOT_LITE &&", ctx_clean
            )
            ctx_clean = re.sub(
                r"\{isLiteFloor\s*&&\s*\(isProUser\s*\?", "{LITE_PROVIEW &&", ctx_clean
            )
            # Also accept the AND-chain form `{isLiteFloor && isProUser &&`
            # as a valid Pro-only-on-Lite gate. Lite users won't render the
            # component at all (no preview lock UX, but no 403 either).
            # Born 2026-05-02 from finding 2 of the brutal-CTO inspection.
            ctx_clean = re.sub(
                r"\{isLiteFloor\s*&&\s*isProUser\s*&&", "{LITE_PROVIEW_ANDCHAIN &&", ctx_clean
            )
            # Now check if the immediately-preceding render block is plain
            # `{isLiteFloor &&`. We look for the LAST `{` opener before usage.
            last_lite_open = ctx_clean.rfind("{isLiteFloor &&")
            last_other_open = max(
                ctx_clean.rfind("{NOT_LITE &&"),
                ctx_clean.rfind("{NOT_LITE_BUT_PRO &&"),
                ctx_clean.rfind("{LITE_PROVIEW &&"),
                ctx_clean.rfind("{LITE_PROVIEW_ANDCHAIN &&"),
            )
            if last_lite_open > last_other_open and last_lite_open > -1:
                lineno = page_text[: usage.start()].count("\n") + 1
                findings.append({
                    "component": comp_name,
                    "page_line": lineno,
                    "calls_pro_endpoints": sorted(called_pro_paths),
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ no Pro-gated endpoint called from Lite-rendered tile")
            return 0
        print(f"✗ {len(findings)} Pro-gated endpoint(s) called from Lite-rendered tiles:")
        for f in findings:
            ep_str = ", ".join(f["calls_pro_endpoints"])
            print(f"  • <{f['component']}> @ page.tsx:{f['page_line']} → {ep_str}")
        print()
        print("Fix: either (a) flip the endpoint to require_merchant_session")
        print("(if the feature is parity-required at $0-60 — see strict")
        print("$0-60 parity rule), OR (b) wrap the component with the")
        print("`{isLiteFloor && (isProUser ? <X /> : <ProLockedTile />)}` pattern")
        print("so Lite users see a padlock instead of an error state.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
