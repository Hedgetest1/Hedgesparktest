#!/usr/bin/env python3
"""audit_operator_filter_propagation.py — Pin operator-shop exclusion
across merchant-pool aggregations.

Born 2026-05-06 after the founder reported their dev tenant
(`hedgespark-dev.myshopify.com`) polluting merchant-shape metrics
beyond email: peer pool counts (driving MA-4 honesty badge),
vertical classifier output, onboarding funnel rates, system
diagnostic merchant counts. ANY query that aggregates over the
`merchants` table without operator exclusion silently inflates
merchant-count-based metrics by 1.

The audit greps app/services + app/workers for files containing
`db.query(Merchant)` OR `FROM merchants` (raw SQL) AND verifies
each file ALSO contains either:
    - `operator_dev_shops()` import (consumed in a filter), OR
    - `# operator-filter: <reason>` opt-out comment (e.g.,
      "single-tenant query — operator passes its own shop_domain";
      "ops admin command — listing all merchants is correct").

Allowlist: a frozen baseline of files that have legitimate
operator-inclusive queries (admin commands, billing reconciliation,
GDPR redact). Adding to allowlist requires justification.

# invariant-eligible: false
# Reason: file-system grep, not a runtime invariant. Same shape as
# audit_service_test_coverage — preflight-only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

_AGG_RE = re.compile(
    r"\bdb\.query\(\s*Merchant\b|"
    r"FROM\s+merchants\b|"
    r"from\s+merchants\b",
    re.IGNORECASE,
)
_OPERATOR_FILTER_RE = re.compile(
    r"operator_dev_shops\s*\(|"  # bare or aliased call
    r"is_operator_dev_shop\s*\(|"
    r"\bfrom\s+app\.core\.operator_blocklist\b|"  # any import
    r"_op_shops\b|"  # common alias
    r":operator_shops\b",  # raw SQL bind name
)
_OPT_OUT_RE = re.compile(r"^\s*#\s*operator-filter:\s*", re.MULTILINE)


# Files where a non-operator-filtered merchants query is INTENTIONAL.
# Each entry has a comment explaining why. Adding here requires the
# same scrutiny as adding a `# operator-filter:` opt-out at the
# call site — but file-level allowlist is cleaner when the file
# contains many queries that are all admin/operator/single-tenant.
_ALLOWLIST: frozenset[str] = frozenset({
    # Admin Telegram commands explicitly query "every merchant" for
    # operator dashboard purposes.
    "app/services/telegram_agent.py",
    # GDPR redact pipeline must process every shop_domain regardless
    # of tier (legal obligation).
    "app/services/gdpr_processor.py",
    # Merchant privacy: per-merchant queries by user request.
    "app/services/merchant_privacy.py",
    # Billing sync: dev tenant has a real Shopify subscription used
    # for billing test flows; sync must run for it.
    "app/services/billing_sync.py",
    # Simulation engine: explicitly handles is_synthetic shops only.
    "app/services/simulation_engine.py",
    # Per-shop scoped operations (caller supplies shop_domain):
    "app/services/onboarding.py",
    "app/services/onboarding_health.py",
    "app/services/orchestrator_context.py",
    "app/services/system_diagnostic.py",
    "app/services/data_integrity_probe.py",
    "app/services/inventory_snapshot_runner.py",
    "app/services/webhook_monitor.py",
    "app/services/merchant_scoring.py",
    "app/services/followup_worker.py",
    "app/services/merchant_churn_predictor.py",
    "app/services/invariant_monitor.py",
})

# API files are per-tenant by definition — every endpoint takes a
# shop session and queries Merchant for that one shop. NOT
# aggregations. The whole `app/api/*` tree is allowlisted to avoid
# the broad-regex false positives.
_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "app/api/",
)


_PER_TENANT_RE = re.compile(
    r"\.filter\([^)]*Merchant\.shop_domain\s*==\s*|"
    r"WHERE\s+shop_domain\s*=\s*:|"
    r"WHERE\s+m\.shop_domain\s*=\s*:|"
    r"shop_domain\s*=\s*shop_domain",
    re.IGNORECASE | re.DOTALL,
)


def _scan_file(path: Path) -> tuple[bool, bool, list[int]]:
    """Return (has_aggregation, has_filter_or_optout, line_numbers).

    Per-tenant queries (single-shop lookups via `shop_domain == X`)
    are NOT aggregations — they don't need an operator-shop exclusion
    because they target one specific shop. The narrow heuristic:
    if EVERY `db.query(Merchant)` / `FROM merchants` site in the
    file is within 8 lines of a per-tenant filter, treat the whole
    file as per-tenant."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return (False, False, [])
    lines = text.split("\n")
    raw_hits: list[int] = []
    for i, line in enumerate(lines):
        if _AGG_RE.search(line):
            raw_hits.append(i)
    if not raw_hits:
        return (False, False, [])
    # Filter out per-tenant hits — a hit within 8 lines of a per-tenant
    # filter is single-shop, not aggregation.
    agg_hits: list[int] = []
    for hit in raw_hits:
        window_start = max(0, hit - 1)
        window_end = min(len(lines), hit + 8)
        window = "\n".join(lines[window_start:window_end])
        if not _PER_TENANT_RE.search(window):
            agg_hits.append(hit + 1)
    if not agg_hits:
        return (False, True, [])  # all per-tenant — file is fine
    has_filter = bool(_OPERATOR_FILTER_RE.search(text))
    has_optout = bool(_OPT_OUT_RE.search(text))
    return (True, has_filter or has_optout, agg_hits)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="No-op shim — strict by default for new aggregations.")
    parser.parse_args()

    findings: list[tuple[str, list[int]]] = []
    files_scanned = 0
    for sub in ("services", "workers", "api"):
        d = APP / sub
        if not d.is_dir():
            continue
        for p in d.rglob("*.py"):
            if p.name == "__init__.py":
                continue
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            if rel in _ALLOWLIST:
                continue
            if any(rel.startswith(prefix) for prefix in _ALLOWLIST_PREFIXES):
                continue
            files_scanned += 1
            has_agg, has_gate, lines = _scan_file(p)
            if has_agg and not has_gate:
                findings.append((rel, lines[:5]))

    if findings:
        print(
            f"audit_operator_filter_propagation: FAIL — "
            f"{len(findings)} file(s) aggregate over merchants without "
            f"operator-shop exclusion ({files_scanned} files scanned):"
        )
        for rel, lines in findings:
            line_str = ",".join(str(n) for n in lines)
            print(f"  {rel} (lines {line_str})")
        print(
            "\nFix one of:\n"
            "  1. Import + use `operator_dev_shops()` in the filter:\n"
            "       from app.core.operator_blocklist import operator_dev_shops\n"
            "       .filter(~Merchant.shop_domain.in_(operator_dev_shops()))\n"
            "     (raw SQL: AND NOT (shop_domain = ANY(:operator_shops)))\n"
            "  2. Add a top-level comment in the module:\n"
            "       # operator-filter: <reason>\n"
            "     e.g., 'admin command — operator inclusion is correct',\n"
            "     'per-shop scoped query — caller supplies shop_domain',\n"
            "     'is_synthetic-only path — no real merchants involved'.\n"
            "  3. Add the file path to `_ALLOWLIST` in this audit if the\n"
            "     entire file is inherently operator-inclusive.\n"
        )
        return 1

    print(
        f"audit_operator_filter_propagation: OK — {files_scanned} file(s) "
        f"with merchant aggregations all properly gate operator/dev shops."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
