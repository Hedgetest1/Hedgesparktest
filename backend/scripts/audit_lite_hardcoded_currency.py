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

import argparse
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

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
    content = safe_read_text(path)
    if content is None:
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


# Auto-fix patterns — deterministic mechanical rewrites ONLY in files
# that declare the safe consumer contract. The contract signature is
# `value: string | number` in a type alias — that means the renderer
# accepts both raw numbers (which it formats via formatMoneyCompact +
# displayCurrency) and pre-formatted strings.
#
# Files WITHOUT the contract (e.g. LiteRarsHero where `value` is
# rendered inline as `{s.value}` without wrapping) are NOT auto-fixed
# even when the regex would match — converting `value: "€680"` to
# `value: 680` there would produce a renderer output of "680" without
# any currency symbol, which is worse than the original mismatch.
# Those hits are flagged as human-needed.
SAFE_CONSUMER_CONTRACT_RE = re.compile(r'value:\s*string\s*\|\s*number')

VALUE_LITERAL_RE = re.compile(
    r'value:\s*"([€£¥₩₹$])\s*([\d,]+(?:\.\d+)?)"'
)


def autofix_file(path: Path) -> tuple[int, list[tuple[int, str]]]:
    """Returns (auto_fixed_count, human_needed_list).
    Auto-fix is gated by the safe-consumer-contract check: only files
    that declare `value: string | number` in a type alias are eligible
    for the value-literal rewrite. Other patterns + non-eligible files
    surface as human-needed.
    """
    content = safe_read_text(path)
    if content is None:
        return (0, [])
    auto_fixed = 0
    is_safe_to_autofix = bool(SAFE_CONSUMER_CONTRACT_RE.search(content))

    if is_safe_to_autofix:
        def replace(m: re.Match) -> str:
            nonlocal auto_fixed
            amount = m.group(2).replace(",", "")
            try:
                num = int(amount) if "." not in amount else float(amount)
            except ValueError:
                return m.group(0)
            auto_fixed += 1
            return f"value: {num}"

        new_content = VALUE_LITERAL_RE.sub(replace, content)

        if auto_fixed > 0:
            path.write_text(new_content)
            content = new_content

    # Re-scan: anything still flagged is human-needed
    human_needed: list[tuple[int, str]] = []
    for idx, line in iter_user_visible_lines(content):
        hit_symbol = SYMBOL_RE.search(line)
        hit_dollar = DOLLAR_DIGIT_RE.search(line)
        if not (hit_symbol or hit_dollar):
            continue
        if any(tok in line for tok in SAFE_TOKENS):
            continue
        human_needed.append((idx, line.strip()[:140]))

    return auto_fixed, human_needed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="compat shim for invariant_monitor — accepted but no-op")
    parser.add_argument("--fix", action="store_true", help="Auto-rewrite mechanical patterns; flag rest as human-needed")
    args = parser.parse_args()

    files = in_scope_files()

    if args.fix:
        total_fixed = 0
        all_human_needed: dict[str, list[tuple[int, str]]] = {}
        for f in files:
            fixed, human = autofix_file(f)
            total_fixed += fixed
            if human:
                all_human_needed[str(f.relative_to(DASHBOARD.parent.parent))] = human
        print(f"auto-fix: {total_fixed} mechanical rewrite(s) applied (value: \"€N\" → value: N pattern)")
        if all_human_needed:
            total_human = sum(len(h) for h in all_human_needed.values())
            print(f"auto-fix: {total_human} hit(s) require human review (text descriptions, sublabels)")
            for path, hits in all_human_needed.items():
                print(f"  {path}:")
                for lineno, snippet in hits[:8]:
                    print(f"    line {lineno}: {snippet}")
                    print(f"      → text rewrite needed (e.g. 'monthly €' → 'monthly revenue')")
            return 1
        return 0

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
    print("  - Run `python scripts/audit_lite_hardcoded_currency.py --fix`")
    print("    for mechanical rewrites (value: \"€N\" → value: N)")
    print("  - For text descriptions, switch to currency-neutral phrasing")
    print("    (e.g. 'monthly revenue' instead of 'monthly €')")
    print("  - If the symbol is intentional, add inline exemption:")
    print("    `{/* audit:hardcoded-currency-ok */}`")
    return 1


if __name__ == "__main__":
    sys.exit(main())
