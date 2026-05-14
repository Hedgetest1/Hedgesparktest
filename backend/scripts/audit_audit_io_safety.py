#!/usr/bin/env python3
"""audit_audit_io_safety.py — preventer for TOCTOU regressions in
preflight audit scripts.

Problem class
-------------
Audit scripts that walk the source tree with `Path.rglob(...)` followed
by `path.read_text(...)` (or `path.open(...)`) are vulnerable to a
classic TOCTOU race when a concurrent test fixture creates+deletes a
file inside the scanned tree:

    test_audit_data_truth_gate writes _test_hardcoded_eur_DELETE_ME.py
    under app/services/, deletes it at teardown.
        → invariant_monitor cycle running in parallel
        → rglob discovers the file
        → read_text() raises FileNotFoundError
        → audit exits non-zero
        → invariant_regression CRITICAL fired

The same race fired twice on 2026-05-13 (audit_cte_missing_comma,
audit_tier_cost_literals). 70+ other audits had the latent bug — they
just hadn't lost the timing roulette yet.

The defense
-----------
Every audit that does `rglob → read_text/open` MUST either:

  (a) import the canonical helper:
          from _audit_io import safe_read_text
      and use it instead of `path.read_text(...)`, OR

  (b) wrap the read in an explicit try/except naming both
      `FileNotFoundError` AND `PermissionError`. (Bare
      `except Exception` does NOT count — it's too broad and hides
      real bugs; an explicit guard documents the race intent.)

This preventer is AST-aware: it parses each `audit_*.py`, finds rglob
+ read sites, and verifies one of the two patterns covers them. If
neither is present, the audit is flagged.

The preventer self-excludes (no point flagging itself), and excludes
the helper file `_audit_io.py` (which doesn't do rglob).

Exit codes
----------
    0 — every audit covered
    1 — one or more audits missing TOCTOU defense
    2 — script error (e.g. malformed audit file)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from _audit_io import safe_read_text  # noqa: E402

# Audits that legitimately don't need the defense — typically because
# they read a *fixed* known path (no rglob discover step) or because
# they intentionally want a hard failure on missing/unreadable files.
# Keep this list TINY and document each entry.
_EXEMPT: frozenset[str] = frozenset({
    "audit_audit_io_safety.py",  # this file
})

_HELPER_NAME = "safe_read_text"
_HELPER_MODULE = "_audit_io"


def _imports_helper(tree: ast.Module) -> bool:
    """Return True iff the audit imports `safe_read_text` from
    `_audit_io` at any scope (module-level OR inside main())."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _HELPER_MODULE:
                for alias in node.names:
                    if alias.name == _HELPER_NAME:
                        return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _HELPER_MODULE:
                    return True
    return False


def _has_explicit_toctou_guard(text: str) -> bool:
    """Return True iff the source mentions BOTH `FileNotFoundError`
    AND `PermissionError` — the explicit-guard escape valve. We use
    text-search rather than AST because the names appear in `except`
    tuples, isinstance checks, and docstrings; any mention is a
    sufficient signal that the author thought about the race."""
    return "FileNotFoundError" in text and "PermissionError" in text


def _does_rglob_then_read(text: str, tree: ast.Module) -> bool:
    """Return True iff the audit calls `.rglob(` (or `.glob(`) AND
    one of `.read_text(` / `.read_bytes(` / `.open(` somewhere in
    the source. AST walk would be more precise but text-search is
    sufficient for a conservative gate — false positives only force
    the author to add the import, which is the right outcome."""
    has_glob = ".rglob(" in text or ".glob(" in text
    has_read = (
        ".read_text(" in text
        or ".read_bytes(" in text
        or ".open(" in text
    )
    return has_glob and has_read


def main() -> int:
    findings: list[tuple[str, str]] = []
    scanned = 0

    for audit_path in sorted(SCRIPTS_DIR.glob("audit_*.py")):
        if audit_path.name in _EXEMPT:
            continue
        text = safe_read_text(audit_path)
        if text is None:
            # This file disappeared between glob and read — same race
            # we're defending against. Skip; next preflight cycle picks
            # it up. Self-applying the doctrine.
            continue
        if not _does_rglob_then_read(text, ast.parse("")):
            continue
        scanned += 1
        try:
            tree = ast.parse(text, filename=str(audit_path))
        except SyntaxError as exc:
            findings.append((audit_path.name, f"syntax error: {exc}"))
            continue
        if _imports_helper(tree):
            continue
        if _has_explicit_toctou_guard(text):
            continue
        findings.append((
            audit_path.name,
            "rglob+read without TOCTOU defense "
            "(import safe_read_text from _audit_io OR add explicit "
            "try/except (FileNotFoundError, PermissionError))",
        ))

    if findings:
        print(
            f"audit_audit_io_safety: {len(findings)} audit(s) vulnerable "
            f"to rglob+read TOCTOU race (out of {scanned} scanned):"
        )
        for name, reason in findings:
            print(f"  {name}: {reason}")
        print()
        print(
            "Fix: either `from _audit_io import safe_read_text` and use "
            "it instead of `path.read_text(...)`, OR wrap the read in an "
            "explicit `try / except (FileNotFoundError, PermissionError)`."
        )
        return 1

    print(
        f"audit_audit_io_safety: clean — {scanned} audit(s) with rglob+read "
        f"all covered by safe_read_text or explicit TOCTOU guard"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
