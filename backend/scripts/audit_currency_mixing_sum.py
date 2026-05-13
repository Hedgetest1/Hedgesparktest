#!/usr/bin/env python
"""
audit_currency_mixing_sum.py — preflight invariant.

Catches the exact bug class shipped in two services before the
2026-04-29 retro: SQL `SUM(total_price)` aggregated across multiple
shop_domain values WITHOUT filtering by single currency, then summed
into a single number labeled `revenue_eur` (or any base currency).

Why it's a bug class
--------------------
shop_orders.total_price is in shop's NATIVE currency. A merchant
running EU (EUR) + US (USD) + UK (GBP) stores has rows in 3 different
denominations. Summing them as a single number without FX conversion
produces meaningless output: €10000 + $8000 + £5000 ≠ €23000.

HedgeSpark deliberately does NOT ship FX conversion (no external
dependency, GDPR-friendlier). The correct pattern is per-currency
totals (`by_currency: {EUR: {...}, USD: {...}}`) returned by the
shared helper `app/services/multi_currency_rollup.py`.

What this audits
----------------
Walks `app/services` and `app/api` for `.py` files. Flags any function
that does ALL of:
  1. Calls `text("SELECT ... SUM(total_price) ... GROUP BY shop_domain")
     OR similar across multiple shops
  2. Does NOT include `currency = :currency` (or equivalent currency
     filter) in the WHERE clause
  3. Does NOT route the result through `multi_currency_rollup.aggregate_by_currency`

Detection is line-window based: SQL string + variable assignment
proximity (within 50 lines). False positives are reduced by the
"multi-shop aggregation" signal (shop_domain in WHERE/GROUP BY,
NOT a single shop_domain = :s filter).

Usage
-----
    ./venv/bin/python scripts/audit_currency_mixing_sum.py
    ./venv/bin/python scripts/audit_currency_mixing_sum.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
SCAN_DIRS = [
    REPO_ROOT / "backend" / "app" / "services",
    REPO_ROOT / "backend" / "app" / "api",
]

# A SQL block that aggregates SUM(total_price) across multiple shops.
# Detected by:
#  - SUM( total_price ) in the SELECT list
#  - shop_domain in GROUP BY (we're returning per-shop)
#    OR shop_domain = ANY(:shops) in WHERE (multi-shop input)
_SQL_SUM_PATTERN = re.compile(
    r"""SUM\s*\(\s*(?:so\.|shop_orders\.)?total_price\s*\)""",
    re.IGNORECASE,
)
_MULTI_SHOP_GROUP_PATTERN = re.compile(
    r"""(?:GROUP\s+BY\s+(?:so\.|shop_orders\.)?shop_domain
        | shop_domain\s*=\s*ANY\s*\(\s*:shops\s*\))""",
    re.IGNORECASE | re.VERBOSE,
)
_CURRENCY_FILTER_PATTERN = re.compile(
    r"""(?:
        currency\s*=\s*:currency
      | currency\s*=\s*COALESCE
      | (?:m\.|merchants\.)?primary_currency
      |
        # GROUP BY ... currency — the SUM result is per-currency-bucketed,
        # so each row in the output is a single-currency sum (never mixed).
        # Caller picks the row matching the shop's primary currency
        # downstream. Born 2026-05-13 after data_integrity_probe.py
        # legitimate GROUP BY (shop_domain, currency) bucketing got
        # flagged. The bug class this audit prevents (single SUM row
        # mixing currencies) is structurally impossible when currency
        # is in the GROUP BY clause.
        #
        # `[\s\S]{0,400}?` is non-greedy + crosses newlines — required
        # because real SQL routinely splits GROUP BY clauses across
        # lines (`GROUP BY\n    shop_domain,\n    currency`). The 400-
        # char bound prevents the match running into the next statement
        # (SQL clauses are typically <100 chars). Agent-review finding
        # 2026-05-13 caught the prior `[^\n]*` form silently passing
        # for single-line patterns while failing for formatted multi-
        # line queries.
        GROUP\s+BY\b[\s\S]{0,400}?\bcurrency\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_SAFE_AGGREGATOR_PATTERN = re.compile(
    r"""(?:
        from\s+app\.services\.multi_currency_rollup
      | aggregate_by_currency\s*\(
      | by_currency\s*[:=]
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _check_text_block(text: str, sql_match_pos: int, window: int = 1500) -> bool:
    """Return True if currency-filter + multi-shop signals are both
    present within `window` chars around the SUM(total_price) site."""
    start = max(0, sql_match_pos - window)
    end = min(len(text), sql_match_pos + window)
    block = text[start:end]
    return (
        _MULTI_SHOP_GROUP_PATTERN.search(block) is not None
    )


@telemetered("audit_currency_mixing_sum")
def audit() -> int:
    findings: list[dict] = []
    for d in SCAN_DIRS:
        for py_file in d.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # File-level safe-aggregator signal — file routes through
            # the shared helper, skip.
            if _SAFE_AGGREGATOR_PATTERN.search(text):
                continue
            for m in _SQL_SUM_PATTERN.finditer(text):
                # Only flag if multi-shop aggregation is in the same SQL.
                if not _check_text_block(text, m.start()):
                    continue
                # Check for currency filter in same SQL block (window).
                start = max(0, m.start() - 1500)
                end = min(len(text), m.start() + 1500)
                block = text[start:end]
                if _CURRENCY_FILTER_PATTERN.search(block):
                    continue
                # Multi-shop SUM(total_price) without currency filter
                # AND without aggregate_by_currency routing → FLAG.
                lineno = text[: m.start()].count("\n") + 1
                findings.append({
                    "file": str(py_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "context": "SUM(total_price) across shops without currency filter",
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ no currency-mixing SUM patterns in app/services|app/api")
            return 0
        print(f"✗ {len(findings)} potential currency-mixing aggregation(s):")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  {f['context']}")
        print()
        print("Fix: filter `WHERE currency = :currency` (single shop's primary)")
        print("OR route output through multi_currency_rollup.aggregate_by_currency()")
        print("which returns per-currency buckets — never summed cross-currency.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
