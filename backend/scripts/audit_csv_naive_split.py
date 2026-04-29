#!/usr/bin/env python
"""
audit_csv_naive_split.py — preflight invariant.

Catches the bug class shipped in two HedgeSpark commits before the
2026-04-29 founder-driven retro: TypeScript/JS CSV parsers that use
`text.split('\\n').split(',')` instead of an RFC 4180 state machine.

Why it's a bug class
--------------------
The naive split breaks on:
  - Quoted commas: `"Beer, IPA"` → cell offset by 1
  - Quoted newlines: `"Line 1\\nLine 2"` → row split mid-quote
  - Escaped quotes: `"O""Brien"` → token mangling

For HedgeSpark export data, the most common breakage is product
titles with commas. Founder catches it post-merchant; preflight
catches it before.

What this audits
----------------
Walks `dashboard/src/app` for `.ts` and `.tsx` files. Flags any file
that:
  1. Fetches a `text/csv` or `text/plain` response (`.text()` call), AND
  2. Subsequently calls `.split(",")` on per-line tokens

Detection is regex-based with a deliberate proximity rule (split-on-
comma must appear in the same file as a `.text()` or `csvText`/`csv`
variable). Files using a proper state-machine parser (`parseCsvRfc4180`,
`parseCsv` extracted helpers, papaparse imports) are exempt.

Exemptions
----------
- `papaparse` import detected → file uses a real CSV library, skip.
- `parseCsvRfc4180` helper invoked OR defined → file uses our own
  state machine, skip.

Usage
-----
    ./venv/bin/python scripts/audit_csv_naive_split.py
    ./venv/bin/python scripts/audit_csv_naive_split.py --json
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
DASHBOARD_DIR = REPO_ROOT / "dashboard" / "src" / "app"

# Signals that a file handles CSV-shaped data:
_CSV_SIGNAL_RE = re.compile(
    r"""(?:
        \.text\(\)               # fetch().then(r => r.text())
      | csv[A-Z][a-z]+           # csvText, csvBlob, csvRows etc.
      | text/csv                 # explicit content-type
    )""",
    re.VERBOSE,
)

# Naive comma-split that's the bug pattern:
_NAIVE_SPLIT_RE = re.compile(
    r"""\.split\s*\(\s*["']\s*,\s*["']\s*\)"""
)

# Exemption signals — file already uses proper CSV handling:
_PROPER_PARSER_RE = re.compile(
    r"""(?:
        from\s+["']papaparse["']
      | parseCsvRfc4180          # our own state machine
      | papaparse\.parse
    )""",
    re.VERBOSE,
)


@telemetered("audit_csv_naive_split")
def audit() -> int:
    findings: list[dict] = []
    for ts_file in DASHBOARD_DIR.rglob("*.ts*"):
        if "node_modules" in ts_file.parts or ".next" in ts_file.parts:
            continue
        try:
            text = ts_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _CSV_SIGNAL_RE.search(text):
            continue  # not a CSV handler
        if _PROPER_PARSER_RE.search(text):
            continue  # uses real parser, skip
        # File handles CSV but no proper parser detected — flag every
        # naive split-on-comma as a hit.
        for m in _NAIVE_SPLIT_RE.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            line_content = text.splitlines()[lineno - 1].strip()
            findings.append({
                "file": str(ts_file.relative_to(REPO_ROOT)),
                "line": lineno,
                "code": line_content[:120],
            })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ no naive CSV split-on-comma in dashboard/src")
            return 0
        print(f"✗ {len(findings)} naive CSV split(s):")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  {f['code']}")
        print()
        print("Fix: route through parseCsvRfc4180() helper (state machine,")
        print("handles quoted commas + quoted newlines + escaped quotes per RFC 4180).")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
