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
TASKS_DIR = BACKEND_ROOT / "app" / "workers" / "tasks"
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


def _extract_fn_doclines(py_path: Path) -> dict[str, str]:
    """MED-14 closure 2026-04-24: for every `_run_*` in agent_worker.py
    return {fn_name: first_line_of_docstring}. Lets the --verify-doclines
    flag detect stale documentation where the code docstring no longer
    matches what the reality map claims the function does."""
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_run_"):
            continue
        doc = ast.get_docstring(node)
        first = (doc or "").strip().splitlines()[0].strip() if doc else ""
        out[node.name] = first
    return out


def _extract_fn_rows_from_md(md_text: str) -> dict[str, str]:
    """Return {fn_name: description_cell} for every row in the agent_worker
    section of the reality map. Description cell is the 2nd column (1st
    is the function name itself)."""
    m = _SECTION_START_RE.search(md_text)
    if not m:
        return {}
    start = m.end()
    rest = md_text[start:]
    nxt = _NEXT_SECTION_RE.search(rest)
    end = start + (nxt.start() if nxt else len(rest))
    section = md_text[start:end]
    out: dict[str, str] = {}
    # Pattern: `| `_run_foo` | description text | ...`
    row_re = re.compile(r"\|\s*\*?\*?`(_run_\w+)`\*?\*?\s*\|\s*([^\|]*?)\s*\|")
    for match in row_re.finditer(section):
        out[match.group(1)] = match.group(2).strip()
    return out


def _docline_mismatch(code_line: str, md_line: str, min_overlap: float = 0.15) -> bool:
    """Heuristic: compare whitespace-tokenized lowercase word sets from
    code docstring first line vs md description cell. If token-overlap
    fraction falls below `min_overlap`, flag as probably-stale. Very
    loose — only fires on clear divergence (complete rewrite of one
    side without updating the other)."""
    if not code_line or not md_line:
        return False  # can't compare, skip
    import re as _re
    code_tokens = set(_re.findall(r"[a-z0-9]+", code_line.lower()))
    md_tokens = set(_re.findall(r"[a-z0-9]+", md_line.lower()))
    if not code_tokens or not md_tokens:
        return False
    overlap = len(code_tokens & md_tokens) / max(len(code_tokens), len(md_tokens))
    return overlap < min_overlap


def _extract_task_modules(tasks_dir: Path) -> set[str]:
    """Every `*_task.py` in app/workers/tasks/ is a scheduled-job unit.
    Exclude __init__ and private leading-underscore helpers."""
    if not tasks_dir.exists():
        return set()
    return {
        p.stem
        for p in tasks_dir.glob("*_task.py")
        if not p.stem.startswith("_")
    }


def _extract_documented_task_modules(md_text: str) -> set[str]:
    """Scan the full docs file for every `<name>_task` mention. Any task
    module shipped to app/workers/tasks/ MUST appear at least once in
    the reality map so the B1-class "duplicate scheduled job" incident
    never repeats. We don't require a specific section or format — a
    single mention anywhere in the file counts as documented."""
    return set(re.findall(r"\b([a-z][a-z0-9_]*_task)\b", md_text))


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv
    verify_doclines = "--verify-doclines" in argv

    if not AGENT_WORKER.exists():
        print(f"audit_scheduled_jobs_map: worker not found — {AGENT_WORKER}")
        return 2
    if not DOC_MAP.exists():
        print(f"audit_scheduled_jobs_map: docs map not found — {DOC_MAP}")
        return 2

    doc_text = DOC_MAP.read_text()

    defined = _extract_defined_fns(AGENT_WORKER)
    documented = _extract_documented_fns(doc_text)
    task_modules = _extract_task_modules(TASKS_DIR)
    documented_tasks = _extract_documented_task_modules(doc_text)

    # MED-14: docstring vs md-description freshness (optional, opt-in
    # via --verify-doclines). Non-blocking by design — false positive
    # rate is too high to gate commits. Operator can run manually
    # before a reality-map sweep.
    stale_doclines: list[tuple[str, str, str]] = []
    if verify_doclines:
        fn_doclines = _extract_fn_doclines(AGENT_WORKER)
        fn_md_rows = _extract_fn_rows_from_md(doc_text)
        for fn, code_line in fn_doclines.items():
            md_line = fn_md_rows.get(fn, "")
            if _docline_mismatch(code_line, md_line):
                stale_doclines.append((fn, code_line[:80], md_line[:80]))

    missing = defined - documented        # in code but not in docs
    stale = documented - defined          # in docs but not in code
    missing_tasks = task_modules - documented_tasks  # task module not in docs

    # Stale task-module detection is intentionally skipped: the regex
    # would match module names referenced as examples, inside removed
    # sections, or in narrative prose. False positives aren't worth the
    # guard. The filesystem side (modules exist → must be documented) is
    # the load-bearing half.

    if not missing and not stale and not missing_tasks:
        # Report stale doclines as non-blocking info (only if --verify-doclines).
        if stale_doclines:
            print(
                f"audit_scheduled_jobs_map: {len(stale_doclines)} possibly-stale "
                f"docstring/docs-row pair(s) (--verify-doclines):"
            )
            for fn, code_line, md_line in stale_doclines[:10]:
                print(f"  - {fn}")
                print(f"      code: {code_line!r}")
                print(f"      docs: {md_line!r}")
            print("(heuristic — review manually, not a failure)")
            print()
        print(
            f"audit_scheduled_jobs_map: clean — {len(defined)} agent_worker "
            f"_run_* helpers + {len(task_modules)} task modules all documented"
        )
        return 0

    print(
        f"audit_scheduled_jobs_map: DRIFT between workers and "
        f"docs/reality_scheduled_jobs.md"
    )
    print()

    if missing:
        print(
            f"  {len(missing)} agent_worker function(s) defined in code but "
            f"NOT documented (add a row to the agent_worker table):"
        )
        for fn in sorted(missing):
            print(f"    + {fn}")
        print()

    if stale:
        print(
            f"  {len(stale)} agent_worker function(s) documented but NOT "
            f"found in code (remove the row, or fix the function name):"
        )
        for fn in sorted(stale):
            print(f"    - {fn}")
        print()

    if missing_tasks:
        print(
            f"  {len(missing_tasks)} task module(s) in app/workers/tasks/ "
            f"but NOT mentioned anywhere in the docs (add at least one "
            f"row under the appropriate worker's section):"
        )
        for mod in sorted(missing_tasks):
            print(f"    + {mod}  (app/workers/tasks/{mod}.py)")
        print()

    print(
        "Fix: edit docs/reality_scheduled_jobs.md."
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
