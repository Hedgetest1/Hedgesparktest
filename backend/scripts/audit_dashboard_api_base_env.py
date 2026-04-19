#!/usr/bin/env python3
"""audit_dashboard_api_base_env.py — block NEXT_PUBLIC_API_BASE drift.

Problem class: the canonical env var is NEXT_PUBLIC_API_BASE_URL (set in
dashboard/.env.production, used by 7+ files). Four recently-added
dashboard files (2026-04-17/18/19) introduced NEXT_PUBLIC_API_BASE
(missing `_URL` suffix) with a hardcoded fallback defaulting to the
prod URL. In prod the fallback masks the drift; in any non-prod
environment the dashboard silently hits the wrong API base.

This audit enforces the single canonical name. Any reference to
`process.env.NEXT_PUBLIC_API_BASE` not followed by `_URL` fails the
commit.

Exit codes:
    0  clean
    1  findings
    2  script error
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"

# Match `process.env.NEXT_PUBLIC_API_BASE` NOT followed by a word char
# (so `NEXT_PUBLIC_API_BASE_URL` is allowed, bare `NEXT_PUBLIC_API_BASE`
# is flagged).
PATTERN = re.compile(r"process\.env\.NEXT_PUBLIC_API_BASE(?!\w)")


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if PATTERN.search(line):
            findings.append((lineno, line.strip()))
    return findings


def main(argv: list[str]) -> int:
    if not DASHBOARD_SRC.exists():
        print(
            f"audit_dashboard_api_base_env: {DASHBOARD_SRC} not found — skip",
            file=sys.stderr,
        )
        return 0

    findings: list[tuple[str, int, str]] = []
    for ext in ("*.ts", "*.tsx"):
        for f in DASHBOARD_SRC.rglob(ext):
            if "node_modules" in f.parts:
                continue
            for lineno, snippet in scan_file(f):
                rel = str(f.relative_to(REPO_ROOT))
                findings.append((rel, lineno, snippet))

    if not findings:
        print(
            "audit_dashboard_api_base_env: clean — every reference uses "
            "NEXT_PUBLIC_API_BASE_URL"
        )
        return 0

    print(
        f"audit_dashboard_api_base_env: {len(findings)} file(s) use the "
        "non-canonical env var NEXT_PUBLIC_API_BASE (without `_URL` suffix)."
    )
    print()
    print("Canonical name: NEXT_PUBLIC_API_BASE_URL")
    print("Fix: replace NEXT_PUBLIC_API_BASE → NEXT_PUBLIC_API_BASE_URL.")
    print()
    for path, lineno, snippet in findings:
        print(f"  {path}:{lineno}  {snippet}")
    print()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_dashboard_api_base_env: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
