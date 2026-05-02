#!/usr/bin/env python3
"""Frontend never-crash architecture preventer.

The dashboard has a 4-layer error-boundary architecture:
  1. global-error.tsx — last line of defense, catches root-layout crash
  2. app/error.tsx     — catches any /app/* route render error
  3. SectionErrorBoundary — per-section scoped boundary
  4. ErrorReporterInstaller — window.onerror + unhandledrejection

If any of these is removed (refactor mistake, merge conflict resolved
the wrong way, accidental deletion), the merchant-facing surface
loses crash protection. This audit verifies every load-bearing file
+ wire is present.

Born 2026-05-02 after the founder mandate "FRONT END CHE NON CRASHA
MAI". The architecture itself is sound; this preventer locks it in
against silent regression.

Usage:
    python3 scripts/audit_route_error_boundary_coverage.py
    Exit 0 = clean. Exit 1 = layer missing.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DASH = REPO / "dashboard" / "src" / "app"

# (path relative to dashboard/src/app, friendly description, required content marker)
# Markers are chosen to distinguish "function defined / called" from
# "function merely imported or commented out". E.g., a regression that
# replaces installGlobalErrorReporter() with a comment must FAIL the
# audit even if the import line still references the symbol.
_REQUIRED_FILES: list[tuple[str, str, str | None]] = [
    (
        "global-error.tsx",
        "Layer 1 — Next.js global error (root layout crash net)",
        "export default function GlobalError",
    ),
    (
        "app/error.tsx",
        "Layer 2 — /app/* route segment error boundary",
        "export default function DashboardError",
    ),
    (
        "components/SectionErrorBoundary.tsx",
        "Layer 3 — per-section component-scope boundary",
        "export class SectionErrorBoundary",
    ),
    (
        "components/ErrorReporterInstaller.tsx",
        "Layer 4 — window.onerror + unhandledrejection installer must "
        "actually CALL installGlobalErrorReporter (not just import it)",
        "installGlobalErrorReporter()",
    ),
    (
        "lib/error-reporter.ts",
        "Frontend error transport (POST /ops/frontend-errors)",
        "export function reportFrontendError",
    ),
    (
        "layout.tsx",
        "Root layout — must mount <ErrorReporterInstaller /> in the JSX tree",
        "<ErrorReporterInstaller",
    ),
]

# Sentry config files — frontend Sentry is the secondary observability
# layer parallel to the self-healing reporter. Both must be present.
_SENTRY_FILES: list[tuple[str, str]] = [
    ("../../sentry.client.config.ts",   "Sentry client SDK config"),
    ("../../sentry.server.config.ts",   "Sentry server SDK config"),
    ("../../sentry.edge.config.ts",     "Sentry edge runtime config"),
    ("../../instrumentation.ts",        "Next.js Sentry instrumentation hook"),
]


def main() -> int:
    failures: list[str] = []

    for rel, desc, marker in _REQUIRED_FILES:
        full = DASH / rel
        if not full.is_file():
            failures.append(f"MISSING: {rel}  — {desc}")
            continue
        if marker:
            try:
                content = full.read_text()
            except Exception as exc:
                failures.append(f"UNREADABLE: {rel} ({exc})")
                continue
            if marker not in content:
                failures.append(
                    f"DEGRADED: {rel} exists but missing '{marker}' "
                    f"— {desc}"
                )

    for rel, desc in _SENTRY_FILES:
        full = (DASH / rel).resolve()
        if not full.is_file():
            failures.append(f"MISSING: {rel}  — {desc}")

    if failures:
        print(
            "FAIL: frontend never-crash architecture has gaps "
            f"({len(failures)} finding(s)):"
        )
        for f in failures:
            print(f"  - {f}")
        print(
            "\nThis architecture is the merchant-facing crash safety net "
            "(CLAUDE.md §4 + reportFrontendError pipeline). Restore the "
            "missing/degraded layer before this commit ships."
        )
        return 1

    layer_count = len(_REQUIRED_FILES) + len(_SENTRY_FILES)
    print(
        f"OK: all {layer_count} frontend never-crash layers present "
        f"({len(_REQUIRED_FILES)} boundary, {len(_SENTRY_FILES)} sentry)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
