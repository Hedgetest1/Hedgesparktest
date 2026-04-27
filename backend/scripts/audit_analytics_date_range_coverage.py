#!/usr/bin/env python3
"""Analytics endpoints date-range coverage preventer.

Born 2026-04-27 from Phase 3B Stage C — the global DateRangePicker
contract requires every Lite-floor analytics endpoint that takes a
`days` query window to ALSO accept the shared DateRangeQuery
dependency, so the merchant's picker affects them.

Without this preventer, the next time a developer adds a new
`/analytics/<x>` endpoint with a `days` param and forgets to wire
`Depends(get_date_range)`, the picker silently no-ops on that surface
— inconsistent merchant UX (some tiles refetch, others don't).

Detection
=========
Scans `app/api/lite_extras.py` for endpoint defs (decorated with
`@router.get`) that have `days: int = Query(...)` in their signature.
Each such endpoint MUST also have a `range_q: DateRangeQuery =
Depends(get_date_range)` parameter.

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
    exempt_count = 0

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
        if has_range:
            wired += 1
            continue

        findings.append(
            f"{TARGET.relative_to(BACKEND)}::{node.name} (route {route_path}) "
            f"has `days` param but missing `range_q: DateRangeQuery = Depends(get_date_range)`"
        )

    if not findings:
        print(
            f"audit_analytics_date_range_coverage: OK — "
            f"{wired}/{total - exempt_count} window-based endpoints wired "
            f"({exempt_count} exempt by design)"
        )
        return 0

    print(
        f"audit_analytics_date_range_coverage: FAIL — "
        f"{len(findings)} endpoint(s) missing date_range integration"
    )
    print()
    for f in findings:
        print(f"  {f}")
    print()
    print("Fix: add `range_q: DateRangeQuery = Depends(get_date_range)` to the")
    print("endpoint signature, then call `resolve_utc_bounds(range_q,")
    print("fallback_days=days, shop_tz=...)` and replace the SQL `NOW() -`")
    print("filter with `created_at >= :start_dt AND created_at < :end_dt_excl`.")
    print("If the endpoint is intentionally NOT range-aware (lifetime, today-")
    print("relative, etc.), add its route path to EXEMPT_ROUTES with a one-line")
    print("rationale.")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
