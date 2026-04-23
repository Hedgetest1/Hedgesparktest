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

# Patterns that reach the canonical env var through different syntaxes.
# 2026-04-23 retro DA hardening: previously only caught `process.env.X`
# dot access. Now also catches bracket access and destructure patterns
# that were bypassing silently:
#   process.env.NEXT_PUBLIC_API_BASE                     — dot (original)
#   process.env['NEXT_PUBLIC_API_BASE']                  — bracket (NEW)
#   process.env["NEXT_PUBLIC_API_BASE"]                  — bracket quoted (NEW)
#   const { NEXT_PUBLIC_API_BASE } = process.env         — destructure (NEW)
#
# In each case the variant WITHOUT a trailing `_URL` is the bug.
PATTERNS = [
    # Dot access — `process.env.NEXT_PUBLIC_API_BASE` not followed by word char.
    re.compile(r"process\.env\.NEXT_PUBLIC_API_BASE(?!\w)"),
    # Bracket access with quotes.
    re.compile(r"""process\.env\[\s*['"]NEXT_PUBLIC_API_BASE['"]\s*\]"""),
    # Destructure from process.env.
    re.compile(
        r"""(?:const|let|var)\s*\{[^{}]*\bNEXT_PUBLIC_API_BASE(?!\w)[^{}]*\}\s*=\s*process\.env\b"""
    ),
]

# Backward-compat single-pattern export for any external caller.
PATTERN = PATTERNS[0]


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in PATTERNS):
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
