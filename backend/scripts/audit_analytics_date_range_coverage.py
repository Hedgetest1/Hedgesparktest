#!/usr/bin/env python3
"""Analytics endpoints date-range coverage preventer.

Born 2026-04-27 from Phase 3B Stage C — the global DateRangePicker
contract requires every Lite-floor analytics endpoint that takes a
`days` query window to ALSO accept the shared DateRangeQuery
dependency, so the merchant's picker affects them.

Extended 2026-04-27 (Phase 3B residual close — comparison toggle):
each `range_q`-wired endpoint MUST also wire the comparison branch.
Accepting `compare_start`/`compare_end` query params but ignoring
them silently is a class of theater bug that ships compare-toggle-
ON without delta data, with no error surfaced. This audit blocks
the regression at preflight.

Detection
=========
1. PRIMARY-RANGE WIRING — Scans `app/api/lite_extras.py` for endpoint
   defs (decorated with `@router.get`) that have `days: int =
   Query(...)` in their signature. Each such endpoint MUST also have
   a `range_q: DateRangeQuery = Depends(get_date_range)` parameter.

2. COMPARE WIRING — Each endpoint with `range_q` MUST either:
     (a) call `resolve_compare_utc_bounds(range_q, ...)`, OR
     (b) reference `range_q.has_compare()` somewhere in its body,
   AND its function body must contain a `compare=` keyword in the
   response constructor (proving the field is populated).

Exemptions (intentional)
========================
- top-customers-ltv: lifetime by definition, range doesn't apply
- customer-churn-forecast: semantics relative-to-today, range
  doesn't fit personal-cadence model

These are listed by route path in the EXEMPT set below; adding new
route to that set requires explicit human reasoning (one-line comment
in this file).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

BACKEND = Path("/opt/wishspark/backend")
TARGET = BACKEND / "app" / "api" / "lite_extras.py"

# Routes intentionally not range-aware. Each entry must carry a one-
# line rationale documented in this file's docstring above.
EXEMPT_ROUTES = {
    "/top-customers-ltv",          # lifetime aggregate
    "/customer-churn-forecast",    # relative-to-today personal cadence
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on findings (default: lenient).")
    args = ap.parse_args()

    if not TARGET.exists():
        print(f"audit_analytics_date_range_coverage: target file not found: {TARGET}")
        return 0

    src = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src)

    findings: list[str] = []
    total = 0
    wired = 0
    compare_wired = 0
    exempt_count = 0

    def _body_uses_compare(fn: ast.FunctionDef) -> bool:
        """Return True iff function body invokes compare-window logic."""
        for sub in ast.walk(fn):
            # range_q.has_compare() reference
            if isinstance(sub, ast.Attribute) and sub.attr == "has_compare":
                if isinstance(sub.value, ast.Name) and sub.value.id == "range_q":
                    return True
            # resolve_compare_utc_bounds(...) call
            if isinstance(sub, ast.Call):
                if isinstance(sub.func, ast.Name) and sub.func.id == "resolve_compare_utc_bounds":
                    return True
        return False

    def _body_populates_compare_kw(fn: ast.FunctionDef) -> bool:
        """Return True iff body has a `compare=...` keyword in any
        response constructor — proves the field is wired through."""
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            for kw in sub.keywords:
                if kw.arg == "compare":
                    return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        # Find @router.get("/path", ...) decorator
        route_path: str | None = None
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr != "get":
                continue
            if not isinstance(dec.func.value, ast.Name):
                continue
            if dec.func.value.id != "router":
                continue
            if not dec.args:
                continue
            if isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                route_path = dec.args[0].value
                break
        if route_path is None:
            continue

        # Has a `days: int = Query(...)` param?
        has_days = False
        has_range = False
        for arg in node.args.args:
            if arg.arg == "days":
                has_days = True
            if arg.arg == "range_q":
                has_range = True

        if not has_days:
            # No days window → date-range integration not applicable
            continue

        total += 1
        if route_path in EXEMPT_ROUTES:
            exempt_count += 1
            continue
        if not has_range:
            findings.append(
                f"{TARGET.relative_to(BACKEND)}::{node.name} (route {route_path}) "
                f"has `days` param but missing `range_q: DateRangeQuery = Depends(get_date_range)`"
            )
            continue

        wired += 1

        # Compare-toggle wiring check (Phase 3B residual close).
        # An endpoint with `range_q` MUST exercise the comparison branch
        # AND populate the `compare` response field. Accepting the params
        # without using them = silent theater.
        uses_compare = _body_uses_compare(node)
        populates_compare = _body_populates_compare_kw(node)
        if uses_compare and populates_compare:
            compare_wired += 1
            continue

        gaps = []
        if not uses_compare:
            gaps.append("no `range_q.has_compare()` / "
                        "`resolve_compare_utc_bounds(range_q, ...)` call")
        if not populates_compare:
            gaps.append("response constructor missing `compare=` keyword")
        findings.append(
            f"{TARGET.relative_to(BACKEND)}::{node.name} (route {route_path}) "
            f"accepts compare params but compare-branch wiring is missing: "
            f"{'; '.join(gaps)}"
        )

    if not findings:
        print(
            f"audit_analytics_date_range_coverage: OK — "
            f"{wired}/{total - exempt_count} window-based endpoints range-wired, "
            f"{compare_wired}/{wired} also compare-wired "
            f"({exempt_count} exempt by design)"
        )
        return 0

    print(
        f"audit_analytics_date_range_coverage: FAIL — "
        f"{len(findings)} endpoint(s) with date-range / compare wiring gaps"
    )
    print()
    for f in findings:
        print(f"  {f}")
    print()
    print("Fix paths:")
    print("  Missing range_q: add `range_q: DateRangeQuery = Depends(get_date_range)`")
    print("    to signature + call `resolve_utc_bounds(range_q, fallback_days=days,")
    print("    shop_tz=...)`.")
    print("  Missing compare wiring: after computing primary aggregate, add a")
    print("    `cmp_b = resolve_compare_utc_bounds(range_q, shop_tz=...)` block")
    print("    that re-runs the same aggregate over the compare window and")
    print("    populates `compare=...` in the response constructor.")
    print("  Intentionally non-range-aware: add the route to EXEMPT_ROUTES with")
    print("    a one-line rationale.")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
