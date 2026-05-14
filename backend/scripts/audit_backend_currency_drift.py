#!/usr/bin/env python3
"""Backend hardcoded-currency preventer.

Born 2026-04-27 after Phase 1 cosmetic audit surfaced 4 backend sites
where "EUR" was hardcoded as the currency tag for user-visible numbers
that were actually in shop-currency. Symptoms:

  - revenue_genome.py:270-275 — AOV/RPC genes labelled "EUR" for any
    merchant (user-visible narrative + unit field corrupted)
  - klaviyo_events.py:235-237 — value_currency hardcoded "EUR" in
    every Klaviyo event payload (downstream LTV/segment math wrong
    for non-EUR merchants)
  - instant_onboarding.py:110 — EUR fallback when an order's currency
    tag is missing (rare but possible on synthetic fixtures)
  - merchant_groups.py:40 — base_currency="EUR" default param

Per app/core/currency.py:17 "USD is safer than EUR because USD is the
Shopify default locale" — that doctrine is now grep-enforced.

Detection
=========
Flags any of these patterns in app/services/*.py + app/api/*.py:

    currency = "EUR"
    currency = 'EUR'
    base_currency: str = "EUR"
    value_currency = "EUR"
    "value_currency": "EUR"
    'value_currency': 'EUR'
    "currency": "EUR"
    'currency': 'EUR'

Excludes
========
- SQLAlchemy column defaults: `default="EUR"`, `server_default="EUR"`
  (DB-level fallbacks, do not leak to user-facing output)
- Comments and docstrings (lines starting with `#`, inside triple-quoted blocks)
- Logging message strings (lines containing `log.` / `logger.`)
- The currency module itself (`app/core/currency.py`)
- Telegram founder-side digest (`app/services/telegram_agent.py`):
  founder is a single user with FOUNDER_PRIMARY_CURRENCY env override
  available — separate concern from merchant-facing code paths
- Any line containing `# audit:eur-default-ok` exemption marker

How to fix
==========
Replace the EUR literal with one of:

  - `get_shop_currency(db, shop_domain) or "USD"` — preferred for
    user-facing response paths
  - `"USD"` — preferred for safe-fallback initial values that will be
    overridden by data
  - Function param `currency: str | None = None` resolved at call site

If the EUR literal is genuinely intentional (rare), add the exemption
marker:

    base_currency: str = "EUR"  # audit:eur-default-ok (EU-default group feature)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

BACKEND = Path("/opt/wishspark/backend")
APP_SERVICES = BACKEND / "app" / "services"
APP_API = BACKEND / "app" / "api"

# Files in scope: services + api
def in_scope_files() -> list[Path]:
    files: list[Path] = []
    for d in (APP_SERVICES, APP_API):
        if d.exists():
            files.extend(sorted(d.glob("*.py")))
    return files

# Files we explicitly skip (founder-side / currency-module itself)
SKIP_FILES = {
    "telegram_agent.py",  # founder-side, FOUNDER_PRIMARY_CURRENCY env override
}

# Approach: any line containing the bare "EUR" literal (single or double
# quoted) is a candidate. Then exemption list filters out legitimate
# uses. This robust-then-narrow design catches positional args, subscript
# assignment, typed defaults, and dict literals with one regex.
EUR_LITERAL = re.compile(r'["\']EUR["\']')

# Lines we never flag — explicit allowlist of legitimate EUR uses.
# Tightened 2026-04-27 after DA-loop on commit d87f986 — the previous
# `getenv|environ.get` blanket was too broad and would have masked a
# future `os.getenv("MERCHANT_DEFAULT_CCY", "EUR")` regression. Now
# the only path to escape is an explicit `# audit:eur-default-ok`
# inline marker + a verbal reason next to it.
EXEMPT_PATTERNS = [
    re.compile(r'\bdefault\s*=\s*["\']EUR["\']'),         # SQLAlchemy default
    re.compile(r'\bserver_default\s*=\s*["\']EUR["\']'),  # SQLAlchemy server_default
    re.compile(r'#\s*audit:eur-default-ok'),              # explicit per-line exemption
    re.compile(r'COALESCE\(currency,\s*["\']EUR["\']\)'),  # SQL fallback (DB-level)
    re.compile(r'["\'](?:USD|EUR|GBP|JPY|CNY|AUD)["\'].*["\'](?:USD|EUR|GBP|JPY|CNY|AUD)["\']'),  # multi-currency map/enum
    re.compile(r'\.get\(currency,'),                      # symbol-map .get() with currency key
    re.compile(r'\bcurrency\s*==\s*["\']EUR["\']'),       # equality check, not assignment
]

# Comments / docstrings — simple line check
def is_comment_or_docstring(line: str, in_docstring: bool) -> bool:
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    if in_docstring:
        return True
    return False


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line) hits."""
    hits: list[tuple[int, str]] = []
    text = safe_read_text(path)
    if text is None:
        return hits

    in_docstring = False
    docstring_quote = None
    for i, line in enumerate(text.splitlines(), start=1):
        # Track docstring state (simple: track triple-quote toggles)
        for q in ('"""', "'''"):
            count = line.count(q)
            if count and (docstring_quote is None or docstring_quote == q):
                # Toggle state per occurrence
                for _ in range(count):
                    if in_docstring and docstring_quote == q:
                        in_docstring = False
                        docstring_quote = None
                    else:
                        in_docstring = True
                        docstring_quote = q

        if is_comment_or_docstring(line, in_docstring):
            continue

        # Skip log/warn/info messages — they're operator-facing, not merchant-facing
        if re.search(r"\blog(?:ger)?\.\w+\s*\(", line):
            continue

        # Skip if no EUR literal at all
        if not EUR_LITERAL.search(line):
            continue

        # Skip if the line is exempted
        if any(p.search(line) for p in EXEMPT_PATTERNS):
            continue

        hits.append((i, line.rstrip()))

    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero on findings (default: lenient).")
    args = ap.parse_args()

    files = in_scope_files()
    flagged: dict[Path, list[tuple[int, str]]] = {}
    total_hits = 0

    for f in files:
        if f.name in SKIP_FILES:
            continue
        hits = scan_file(f)
        if hits:
            flagged[f] = hits
            total_hits += len(hits)

    if not flagged:
        print(f"audit_backend_currency_drift: OK — {len(files)} backend files scanned, no hardcoded EUR in user-facing paths")
        return 0

    print(f"audit_backend_currency_drift: FAIL — {total_hits} hardcoded-currency hit(s) across {len(flagged)} file(s)")
    print()
    for path, hits in flagged.items():
        rel = path.relative_to(BACKEND)
        for ln, line in hits:
            print(f"  {rel}:{ln}: {line.strip()}")
    print()
    print("Fix:")
    print("  - replace with `get_shop_currency(db, shop) or \"USD\"` (response paths)")
    print("  - replace with `\"USD\"` (safe-fallback initial values)")
    print("  - or annotate intentional exemption: `# audit:eur-default-ok`")

    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
