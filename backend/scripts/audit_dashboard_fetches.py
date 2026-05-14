#!/usr/bin/env python
"""
audit_dashboard_fetches.py — Tier 3.2: find raw fetch() calls in the
dashboard that target /pro, /merchant, or /analytics routes and bypass
the generated `apiClient` from openapi-fetch.

Policy: every call to a typed-contract endpoint must go through
`apiClient.GET/POST/PATCH/DELETE("/pro/...")`. That path pulls types
from the auto-generated `api-types.ts`, which is regenerated from the
backend OpenAPI schema by `npm run api:types`. A bare fetch call to
a /pro path skips compile-time URL + query + response validation —
exactly the class of drift bug Tier 3 exists to close.

What the audit does
-------------------
Walks dashboard/src/app for .ts and .tsx files and regex-matches
`fetch(` call sites whose URL string literal (interpreted as-is) ends
with `/pro/`, `/merchant/`, or `/analytics/` paths. Template literals
with `${API_BASE}` / `${apiBase}` / `${API}` are supported — we only
check the path portion after the interpolation.

Exemptions
----------
* `api-client.ts` itself — the typed client wraps fetch internally.
* Any fetch inside a comment block (we check "//" and "/*" prefixes).
* Fetches to /public/, /auth/, /track/, /onboarding/, /webhooks/,
  /chat/, /agency/, /ops/, /system/ — these are not target prefixes.

Usage:
    ./venv/bin/python scripts/audit_dashboard_fetches.py
    ./venv/bin/python scripts/audit_dashboard_fetches.py --detail
    ./venv/bin/python scripts/audit_dashboard_fetches.py --strict
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter, defaultdict
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

DASHBOARD_ROOT = pathlib.Path("/opt/wishspark/dashboard/src/app")
SKIP_FILES = {"api-client.ts", "api-types.ts"}
SKIP_DIRS = {"node_modules", ".next"}

# Match `fetch(` followed by a string (template or single/double quoted)
# that contains /pro/, /merchant/, or /analytics/ as a path segment.
# A leading negative-lookbehind excludes `apiFetch(` / `anotherFetch(`
# wrappers — those already route through the typed client via their
# own helper. Case-sensitive so only lowercase `fetch` matches.
#
# 2026-04-23 retro DA — KNOWN BYPASS: a 2-step helper call like
#     const url = buildUrl('/pro/xxx');
#     fetch(url);
# escapes this audit because the string literal is in the helper, not
# in the fetch call. The bypass requires a specific anti-pattern
# (hand-rolling URL construction + bypassing apiClient) that does NOT
# appear in the current dashboard/src/. Add a cross-line variable-flow
# check if this pattern ever emerges. Documented trigger for fix:
# any PR that adds `build_url` / `buildEndpoint` / `makeUrl` style
# helpers in dashboard/src/ must simultaneously teach this audit to
# follow variable assignments. Logged in session state.
FETCH_RE = re.compile(
    r"""(?<![A-Za-z0-9_$])fetch\s*\(\s*[`'"]"""
    r"""(?P<url>[^`'"]+)""",
)
TARGET_RE = re.compile(r"/(pro|merchant|analytics)/")


class Finding:
    __slots__ = ("file", "line", "url")

    def __init__(self, file: str, line: int, url: str):
        self.file = file
        self.line = line
        self.url = url


def scan_file(path: pathlib.Path) -> list[Finding]:
    if path.name in SKIP_FILES:
        return []
    text = safe_read_text(path)
    if text is None:
        return []
    findings: list[Finding] = []
    rel = path.relative_to(DASHBOARD_ROOT.parent.parent).as_posix()

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for m in FETCH_RE.finditer(line):
            url = m.group("url")
            # Strip ${...} interpolation placeholders to expose the path
            cleaned = re.sub(r"\$\{[^}]+\}", "", url)
            if not TARGET_RE.search(cleaned):
                continue
            findings.append(Finding(rel, lineno, cleaned))
    return findings


def walk() -> list[Finding]:
    findings: list[Finding] = []
    if not DASHBOARD_ROOT.exists():
        return findings
    for path in DASHBOARD_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".ts", ".tsx"):
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        findings.extend(scan_file(path))
    return findings


@telemetered("audit_dashboard_fetches")
def main() -> int:
    findings = walk()
    by_file = defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    print(f"audit_dashboard_fetches: scanned {DASHBOARD_ROOT}")
    print(f"  bare fetch() to /pro|/merchant|/analytics: {len(findings)}")
    print()

    if findings:
        ranked = sorted(by_file.items(), key=lambda kv: len(kv[1]), reverse=True)
        print("Top files by bare-fetch count:")
        for file, items in ranked[:20]:
            print(f"  {len(items):3d}  {file}")
        print()

    if "--detail" in sys.argv and findings:
        print("All sites:")
        for f in sorted(findings, key=lambda x: (x.file, x.line)):
            print(f"  {f.file}:{f.line}  {f.url}")

    strict = "--strict" in sys.argv
    if strict and findings:
        print(f"FAIL: {len(findings)} bare fetch() calls remain (target: 0)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
