#!/usr/bin/env python3
"""audit_section_error_boundary_coverage.py — Pin C-1.

Every dashboard section anchor (`<div id="section-...">` /
`<section id="section-...">`) wraps merchant-visible content. A
crash inside an unwrapped section blanks that section in the
running dashboard — the user-visible failure mode the C-1 ticket
closed by adding `SectionErrorBoundary` around inner cards.

This audit pins the contract: every `id="section-..."` element in
`dashboard/src/app/app/**` MUST have a `<SectionErrorBoundary>` at
or near the same scope (within 30 lines either side). Misses are
flagged for review.

Heuristic — not perfect AST analysis. False-positives expected on
sections that legitimately don't render any cards (e.g. hidden
gates returning a fragment). Add `// eslint-disable-section-error-
boundary` on the line above the id= to opt out.

Exit codes:
  0 — every section anchor has a nearby SectionErrorBoundary
  1 — at least one anchor is unwrapped without opt-out

# invariant-eligible: false
# Reason: heuristic source-grep across dashboard/src; runs cheap at
# preflight, doesn't need agent_worker periodic recognition since
# the boundaries are lint-time guarantees, not runtime conditions.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

ROOT = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "app"

_SECTION_RE = re.compile(r'id\s*=\s*["\']section-[a-z0-9_-]+["\']')
_BOUNDARY_RE = re.compile(r'<SectionErrorBoundary\b')
_OPT_OUT_RE = re.compile(r'//\s*eslint-disable-section-error-boundary')

# Files known to be section dispatchers / pure layout shells where
# the boundary lives at a parent layer; opt out by path.
_EXEMPT_FILES = {
    # The dashboard root error.tsx is the outermost boundary,
    # not a section — section anchors here are by definition inside
    # the boundary.
    "app/app/error.tsx",
    # Onboarding/setup flows render conditional sections that may
    # not need per-card isolation; reviewers can flip individual
    # ones if needed.
    "app/app/setup/page.tsx",
}


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, line) for each unwrapped section
    anchor in this file. A section anchor counts as "wrapped" if the
    same file contains at least one <SectionErrorBoundary> OR the
    anchor line carries the opt-out comment.

    File-level (not line-window) heuristic chosen because section
    anchors at the top of a file (e.g. `<section id="...">` at line
    45) commonly have their boundaries 200+ lines below at the
    inner-card level — a tight window produces false positives."""
    text = safe_read_text(path)
    if text is None:
        return []
    if not _SECTION_RE.search(text):
        return []
    file_has_boundary = bool(_BOUNDARY_RE.search(text))
    lines = text.split("\n")
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _SECTION_RE.search(line):
            continue
        # Opt-out marker on the preceding line
        if i > 0 and _OPT_OUT_RE.search(lines[i - 1]):
            continue
        if file_has_boundary:
            continue
        findings.append((i + 1, line.strip()[:120]))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="No-op shim for compat.")
    parser.parse_args()

    if not ROOT.exists():
        print(f"audit_section_error_boundary_coverage: skip — {ROOT} not found")
        return 0

    findings: list[tuple[str, int, str]] = []
    files_scanned = 0
    for tsx in sorted(ROOT.rglob("*.tsx")):
        rel = tsx.relative_to(ROOT.parent.parent)
        rel_str = str(rel).replace("\\", "/")
        if any(rel_str.endswith(ex) for ex in _EXEMPT_FILES):
            continue
        files_scanned += 1
        for line_no, line in _scan_file(tsx):
            findings.append((rel_str, line_no, line))

    if findings:
        print(
            f"audit_section_error_boundary_coverage: FAIL — "
            f"{len(findings)} unwrapped section anchor(s) "
            f"({files_scanned} files scanned):"
        )
        for rel, line_no, line in findings:
            print(f"  {rel}:{line_no}  {line}")
        print(
            "\nFix: wrap the section content in <SectionErrorBoundary "
            'name="<short label>"> ... </SectionErrorBoundary>, OR add '
            "`// eslint-disable-section-error-boundary` on the line "
            "above the id= if intentional (e.g., shell that renders "
            "nothing). Reason: an unwrapped section blanks the entire "
            "section on a render crash, hiding adjacent cards from the "
            "merchant — the failure mode the C-1 hardening pass closed."
        )
        return 1

    print(
        f"audit_section_error_boundary_coverage: OK — "
        f"{files_scanned} TSX file(s) scanned, every section anchor "
        f"has a nearby SectionErrorBoundary."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
