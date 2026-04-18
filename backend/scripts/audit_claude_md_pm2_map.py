#!/usr/bin/env python3
"""audit_claude_md_pm2_map.py — keep CLAUDE.md §6 in sync with the PM2
apps actually defined in ecosystem.config.js.

Problem class: CLAUDE.md §6 catalogs every long-running process (name +
script + cycle). Operators grep that table first when debugging "why is
X not running". If someone adds a new worker to ecosystem.config.js but
forgets to update §6, the doctrine silently drifts and a future Claude
(or human) triaging an outage wastes minutes hunting for a process that
"should" exist but doesn't — or vice versa, a documented process that
was deleted without cleanup.

This script extracts the set of app `name:` entries from
ecosystem.config.js and the first-column process names from the §6 table,
and fails on drift in either direction.

Exit codes:
    0  map in sync
    1  drift detected
    2  script error

Use `--warn-only` to print findings without failing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
ECOSYSTEM = REPO_ROOT / "ecosystem.config.js"

# §6 header then the PM2 table. The table ends at the next blank-line-
# followed-by-non-pipe or the next "##"/"---" section break.
_SECTION_START_RE = re.compile(r"^###\s+PM2 processes\b", re.MULTILINE)
_TABLE_ROW_NAME_RE = re.compile(r"\|\s*(wishspark-[a-z0-9-]+)\s*\|")

# ecosystem.config.js uses `name: "wishspark-xyz"` per app.
_APP_NAME_RE = re.compile(r'name\s*:\s*"(wishspark-[a-z0-9-]+)"')


def _extract_doc_names(md_text: str) -> set[str]:
    m = _SECTION_START_RE.search(md_text)
    if not m:
        return set()
    start = m.end()
    rest = md_text[start:]
    # Stop at the next `## ` top-level section or `---` horizontal rule.
    stop_re = re.compile(r"^(?:##\s+|---\s*$)", re.MULTILINE)
    stop = stop_re.search(rest)
    end = start + (stop.start() if stop else len(rest))
    section = md_text[start:end]
    return set(_TABLE_ROW_NAME_RE.findall(section))


def _extract_ecosystem_names(js_text: str) -> set[str]:
    return set(_APP_NAME_RE.findall(js_text))


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv

    if not CLAUDE_MD.exists():
        print(f"audit_claude_md_pm2_map: CLAUDE.md not found — {CLAUDE_MD}")
        return 2
    if not ECOSYSTEM.exists():
        print(f"audit_claude_md_pm2_map: ecosystem not found — {ECOSYSTEM}")
        return 2

    doc_names = _extract_doc_names(CLAUDE_MD.read_text())
    eco_names = _extract_ecosystem_names(ECOSYSTEM.read_text())

    if not doc_names:
        print("audit_claude_md_pm2_map: could not parse §6 PM2 table — "
              "has the section header changed?")
        return 2
    if not eco_names:
        print("audit_claude_md_pm2_map: could not parse ecosystem.config.js "
              "apps — has the name: format changed?")
        return 2

    missing = eco_names - doc_names  # running in PM2, not in CLAUDE.md
    stale = doc_names - eco_names    # in CLAUDE.md, not in ecosystem

    if not missing and not stale:
        print(
            f"audit_claude_md_pm2_map: clean — {len(doc_names)} PM2 "
            f"processes all documented and running"
        )
        return 0

    print("audit_claude_md_pm2_map: DRIFT between ecosystem.config.js and "
          "CLAUDE.md §6")
    print()

    if missing:
        print(
            f"  {len(missing)} PM2 process(es) in ecosystem.config.js but "
            f"NOT in CLAUDE.md §6 (add table row with script + cycle):"
        )
        for name in sorted(missing):
            print(f"    + {name}")
        print()

    if stale:
        print(
            f"  {len(stale)} process(es) in CLAUDE.md §6 but NOT in "
            f"ecosystem.config.js (remove row — process was deleted):"
        )
        for name in sorted(stale):
            print(f"    - {name}")
        print()

    print("Fix: edit CLAUDE.md §6 — the '### PM2 processes' table.")
    print("Drift here causes wasted triage time during outages — §6 is "
          "the first place an operator looks.")

    if warn_only:
        print("\n--warn-only: not failing the audit")
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_claude_md_pm2_map: script error — {exc}", file=sys.stderr)
        sys.exit(2)
