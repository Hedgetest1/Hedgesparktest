#!/usr/bin/env python3
"""Lite-floor hardcoded-currency preventer.

Born 2026-04-26 after `verify_lite_dashboard_e2e.js` surfaced a real
runtime currency leak (5 hardcoded € symbols in user-visible JSX text
on Lite components). The runtime E2E catches the class but only when
manually invoked. This audit moves the catch to preflight time so a
new hardcoded €/$/£ in a Lite-floor user-facing string fails the
commit before it ships.

Scope
=====
Lite-floor components only — files matching:
  - dashboard/src/app/components/Lite*.tsx
  - dashboard/src/app/app/page.tsx (lite sections; flagged on whole file
    but the audit message tells the developer where the symbol is)

Why not landing/pricing/install: those pages legitimately hardcode the
SaaS subscription price in € (e.g. "€39/mo") because the SUBSCRIPTION
is universally priced regardless of the merchant's shop currency. The
Lite floor renders MERCHANT-SPECIFIC content where every money figure
must match the shop's currency.

Detection
=========
Scans for currency-symbol-followed-by-digit patterns:
  €123, $123, £123, ¥123, ₩123, ₹123
  "€123", '€123' (string literals)
  >€123<  (JSX text content)

Excludes:
- Lines that are JS/TSX comments (// ... or inside /* ... */)
- Lines that are inside a `{/* ... */}` JSX comment block
- Lines containing `formatMoney`, `formatMoneyCompact`,
  `formatCurrency`, `Intl.NumberFormat`, `currency_symbol`
- Lines explicitly exempted with `// audit:hardcoded-currency-ok`
  (use sparingly — only when the symbol is intentional)

How to apply
============
If a line legitimately needs a hardcoded symbol (e.g. a brand
slogan that includes a €, or a literal price), add the inline
exemption comment:

    "We've recovered €1M for merchants" {/* audit:hardcoded-currency-ok */}

Otherwise, fix by:
- Switching to `formatMoneyCompact(amount, displayCurrency)` — the
  shared helper at `dashboard/src/app/app/_lib/formatters.ts`
- Or using currency-neutral phrasing: "money amount", "revenue",
  "profit" etc.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DASHBOARD = Path("/opt/wishspark/dashboard/src")
COMPONENTS_DIR = DASHBOARD / "app" / "components"
PAGE_TSX = DASHBOARD / "app" / "app" / "page.tsx"

# Files in scope: Lite components + the main /app render
def in_scope_files() -> list[Path]:
    files: list[Path] = []
    if PAGE_TSX.exists():
        files.append(PAGE_TSX)
    files.extend(sorted(COMPONENTS_DIR.glob("Lite*.tsx")))
    return files

# What we flag:
#   1. €/£/¥/₩/₹ symbols anywhere (these are NOT used as code syntax
#      in TS/JSX so any occurrence in non-comment text is suspicious)
#   2. $ followed by digit or whitespace+digit (e.g. "$5", "$ 5")
#      — but NOT bare $, ${, or "$" inside template literal syntax
SYMBOL_RE = re.compile(r'[€£¥₩₹]')
DOLLAR_DIGIT_RE = re.compile(r'(?<!\$)\$(?!\{)\s*\d')

# Lines we never flag (they are legitimately formatting currency)
SAFE_TOKENS = (
    "formatMoney",         # formatMoneyCompact / formatMoneyFull
    "formatCurrency",
    "Intl.NumberFormat",
    "currency_symbol",
    "CURRENCY_SYMBOLS",
    "audit:hardcoded-currency-ok",  # explicit per-line exemption
)

# Block tracking — ignore lines inside JSDoc/comment blocks
def iter_user_visible_lines(content: str):
    """Yield (lineno, line) skipping comment blocks (/* */, JSDoc),
    JSX comment blocks ({/* ... */}), and full-line // comments.
    A best-effort stripper: handles the common cases. Edge cases
    can be exempted via SAFE_TOKENS.audit:hardcoded-currency-ok.
    """
    in_block_comment = False
    in_jsx_comment = False
    for idx, raw in enumerate(content.splitlines(), start=1):
        line = raw
        s = line.lstrip()

        # ── JSX comment block tracking ──
        if "{/*" in line and "*/}" not in line:
            in_jsx_comment = True
            continue
        if in_jsx_comment:
            if "*/}" in line:
                in_jsx_comment = False
            continue
        # Single-line JSX comment
        if "{/*" in line and "*/}" in line:
            # Strip the JSX comment substring before scanning
            line = re.sub(r"\{/\*.*?\*/\}", "", line)

        # ── /* ... */ block comment tracking ──
        if "/*" in line and "*/" not in line:
            # check it's not a JSX comment (already handled)
            if "{/*" not in line:
                in_block_comment = True
                continue
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        # Single-line /* ... */
        if "/*" in line and "*/" in line and "{/*" not in line:
            line = re.sub(r"/\*.*?\*/", "", line)

        # ── Full-line `//` comment or `*` JSDoc continuation ──
        if s.startswith("//") or s.startswith("*"):
            continue

        # ── Inline `//` — strip from line ──
        # Avoid matching // inside string literals naively; for our
        # purposes a heuristic suffices since SAFE_TOKENS handles
        # any false positive that survives.
        if "//" in line and "://" not in line:
            line = line.split("//", 1)[0]

        yield idx, line


def scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        content = path.read_text()
    except OSError:
        return findings

    for idx, line in iter_user_visible_lines(content):
        hit_symbol = SYMBOL_RE.search(line)
        hit_dollar = DOLLAR_DIGIT_RE.search(line)
        if not (hit_symbol or hit_dollar):
            continue
        if any(tok in line for tok in SAFE_TOKENS):
            continue
        findings.append((idx, line.strip()[:140]))
    return findings


def main() -> int:
    files = in_scope_files()
    flagged: dict[str, list[tuple[int, str]]] = {}
    for f in files:
        hits = scan_file(f)
        if hits:
            flagged[str(f.relative_to(DASHBOARD.parent.parent))] = hits

    if not flagged:
        print(f"audit_lite_hardcoded_currency: OK — {len(files)} Lite-floor files scanned, no hardcoded currency in user-visible text")
        return 0

    total_hits = sum(len(h) for h in flagged.values())
    print(f"audit_lite_hardcoded_currency: FAIL — {total_hits} hardcoded currency hit(s) across {len(flagged)} file(s)")
    for path, hits in flagged.items():
        print(f"  {path}:")
        for lineno, snippet in hits[:8]:
            print(f"    line {lineno}: {snippet}")
        if len(hits) > 8:
            print(f"    ...and {len(hits) - 8} more")
    print()
    print("How to fix:")
    print("  - Switch to formatMoneyCompact(amount, displayCurrency)")
    print("    [import from dashboard/src/app/app/_lib/formatters.ts]")
    print("  - Or use currency-neutral phrasing (e.g. 'monthly revenue'")
    print("    instead of 'monthly €')")
    print("  - If the symbol is intentional, add inline exemption:")
    print("    `{/* audit:hardcoded-currency-ok */}`")
    return 1


if __name__ == "__main__":
    sys.exit(main())
