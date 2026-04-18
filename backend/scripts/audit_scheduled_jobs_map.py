#!/usr/bin/env python3
"""audit_scheduled_jobs_map.py — keep docs/reality_scheduled_jobs.md in sync
with the actual `_run_*` helpers in app/workers/agent_worker.py.

Problem class: agent_worker.py contains 40+ `_run_*` scheduled sub-tasks.
A reality map (docs/reality_scheduled_jobs.md) catalogs them so Claude
doesn't propose duplicate jobs (the 2026-04-18 B1 failure). The map is
structurally useful only while it is current.

This script guarantees it stays current by refusing to commit when:
    1. A `def _run_foo()` exists in agent_worker.py but the docs table
       lacks a row for `_run_foo`. → Missing documentation: add a row.
    2. A row for `_run_bar` exists in the docs table but the function
       was renamed/removed. → Stale entry: remove the row.

Exit codes:
    0  map in sync
    1  drift detected
    2  script error

Use `--warn-only` to print findings without failing the audit.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
AGENT_WORKER = BACKEND_ROOT / "app" / "workers" / "agent_worker.py"
DOC_MAP = REPO_ROOT / "docs" / "reality_scheduled_jobs.md"

# The docs file uses a markdown table section for agent_worker's sub-tasks.
# That section starts after "## Internal sub-tasks inside agent_worker.py"
# and ends at the next "## " header.
_SECTION_START_RE = re.compile(
    r"^##\s+Internal sub-tasks inside agent_worker\.py\b", re.MULTILINE
)
_NEXT_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)

# Within the agent_worker section, every row that documents a function
# begins like:  | `_run_xyz` |   or   | **`_run_xyz`** |
_TABLE_ROW_FN_RE = re.compile(r"\|\s*\*?\*?`(_run_\w+)`")


def _extract_documented_fns(md_text: str) -> set[str]:
    m = _SECTION_START_RE.search(md_text)
    if not m:
        return set()
    start = m.end()
    # Find the first "## " AFTER the section start.
    rest = md_text[start:]
    nxt = _NEXT_SECTION_RE.search(rest)
    end = start + (nxt.start() if nxt else len(rest))
    section = md_text[start:end]
    return set(_TABLE_ROW_FN_RE.findall(section))


def _extract_defined_fns(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_run_"):
                names.add(node.name)
    return names


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv

    if not AGENT_WORKER.exists():
        print(f"audit_scheduled_jobs_map: worker not found — {AGENT_WORKER}")
        return 2
    if not DOC_MAP.exists():
        print(f"audit_scheduled_jobs_map: docs map not found — {DOC_MAP}")
        return 2

    defined = _extract_defined_fns(AGENT_WORKER)
    documented = _extract_documented_fns(DOC_MAP.read_text())

    missing = defined - documented  # in code but not in docs
    stale = documented - defined    # in docs but not in code

    if not missing and not stale:
        print(
            f"audit_scheduled_jobs_map: clean — {len(defined)} agent_worker "
            f"_run_* helpers all documented"
        )
        return 0

    print(
        f"audit_scheduled_jobs_map: DRIFT between agent_worker.py and "
        f"docs/reality_scheduled_jobs.md"
    )
    print()

    if missing:
        print(
            f"  {len(missing)} function(s) defined in code but NOT documented "
            f"(add a row to docs/reality_scheduled_jobs.md):"
        )
        for fn in sorted(missing):
            print(f"    + {fn}")
        print()

    if stale:
        print(
            f"  {len(stale)} function(s) documented but NOT found in code "
            f"(remove the row, or fix the function name):"
        )
        for fn in sorted(stale):
            print(f"    - {fn}")
        print()

    print(
        "Fix: edit docs/reality_scheduled_jobs.md — the "
        "'Internal sub-tasks inside agent_worker.py' table."
    )
    print(
        "The map is load-bearing (see 2026-04-18 B1 incident); drift is a "
        "structural bug, not a documentation nit."
    )

    if warn_only:
        print("\n--warn-only: not failing the audit")
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_scheduled_jobs_map: script error — {exc}", file=sys.stderr)
        sys.exit(2)
