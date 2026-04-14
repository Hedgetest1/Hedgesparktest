#!/usr/bin/env python
"""
audit_dead_endpoints.py — Flag backend API routes that no dashboard
code and no test references. Candidates for deletion, consolidation,
or explicit deprecation.

The script walks app.main.app.routes, collects every (method, path)
pair, then greps:
  - dashboard/src/app/**/*.ts{,x} for string occurrences of the path
  - backend/tests/**/*.py for string occurrences of the path

A path that matches NEITHER is orphan. False positives are possible
when the path template has dynamic segments (e.g. /foo/{id}) and the
frontend constructs the URL via template literals — those are caught
by stripping the leading /{...} placeholders and searching for the
literal prefix.

Exits 0 regardless; this is an informational survey, not a gate.
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, "/opt/wishspark/backend")

DASHBOARD_ROOTS = [
    pathlib.Path("/opt/wishspark/dashboard/src"),
]
TEST_ROOTS = [
    pathlib.Path("/opt/wishspark/backend/tests"),
]

_IGNORE_METHODS = {"HEAD", "OPTIONS"}
# Internal / always-expected routes we know are correct infra
_INFRA_PATHS = {
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def _collect_routes() -> list[tuple[str, str]]:
    from app.main import app
    out: list[tuple[str, str]] = []
    for r in app.routes:
        if not (hasattr(r, "path") and hasattr(r, "methods")):
            continue
        path = str(r.path)
        methods = [m for m in (r.methods or set()) if m not in _IGNORE_METHODS]
        for m in methods:
            out.append((m, path))
    return sorted(set(out))


def _search_strings(path: str) -> list[str]:
    """
    Return the list of substrings to search for in consumer code to
    confirm this route is referenced anywhere.

    Strategy: any route with `{param}` segments is hard to match
    literally because the caller builds it via template literals.
    We return TWO candidates:
      - the longest literal prefix up to the first `{param}`
      - the longest literal suffix after the last `{param}`
    The caller accepts a match if EITHER substring appears next to
    the other (combined with a heuristic later), or we fall back to
    simply requiring both to co-occur in the same file.

    For a fully-literal route we return the whole path.
    """
    parts = re.split(r"/\{[^}]+\}", path)
    parts = [p for p in parts if p]  # drop empty
    if not parts:
        return [path]
    if len(parts) == 1:
        # Either fully literal (no params) or only tail params
        return [parts[0].rstrip("/") or path]
    # Multiple literal chunks: use the prefix and the last chunk
    return [parts[0].rstrip("/"), parts[-1].rstrip("/")]


def _file_contains(haystack_files: list[pathlib.Path], needle: str) -> bool:
    if not needle:
        return False
    for f in haystack_files:
        try:
            if needle in f.read_text(errors="ignore"):
                return True
        except Exception:
            continue
    return False


def _any_file_contains_all(haystack_files: list[pathlib.Path], needles: list[str]) -> bool:
    """True if any single file contains every string in needles."""
    if not needles:
        return False
    for f in haystack_files:
        try:
            content = f.read_text(errors="ignore")
        except Exception:
            continue
        if all(n in content for n in needles if n):
            return True
    return False


def main() -> int:
    routes = _collect_routes()
    print(f"Scanning {len(routes)} (method, path) pairs…\n")

    dashboard_files: list[pathlib.Path] = []
    for root in DASHBOARD_ROOTS:
        dashboard_files += list(root.rglob("*.ts"))
        dashboard_files += list(root.rglob("*.tsx"))

    test_files: list[pathlib.Path] = []
    for root in TEST_ROOTS:
        test_files += list(root.rglob("*.py"))

    findings: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    for method, path in routes:
        if path in _INFRA_PATHS:
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)

        # Try the full literal path first (catches routes without params
        # + any caller that uses the verbatim string)
        if _file_contains(dashboard_files, path) or _file_contains(test_files, path):
            continue

        # For parameterized routes, search for the literal chunks. If a
        # single file contains ALL literal chunks, assume it's the caller
        # building the URL via template literal.
        needles = _search_strings(path)
        if _any_file_contains_all(dashboard_files, needles):
            continue
        if _any_file_contains_all(test_files, needles):
            continue

        findings.append((method, path))

    # Group by path prefix for readability
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for method, path in findings:
        prefix = "/" + path.strip("/").split("/")[0]
        groups[prefix].append((method, path))

    if not findings:
        print("✅ Every route is referenced by the dashboard or a test")
        return 0

    print(f"⚠️  {len(findings)} routes with NO dashboard/test reference\n")
    for prefix in sorted(groups):
        print(f"  {prefix}/…")
        for method, path in sorted(groups[prefix]):
            print(f"    {method:6s} {path}")
        print()
    print("Notes:")
    print("  - False positives happen when the caller constructs the URL")
    print("    via template literals with dynamic segments.")
    print("  - Infra routes (/, /openapi.json, /docs, /redoc) are skipped.")
    print("  - Cron / Telegram webhook / scheduled-job routes may appear")
    print("    here even though they are legitimately called from outside.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
