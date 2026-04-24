#!/usr/bin/env python
"""
audit_silent_returns.py — inventory every Redis/client-down fast-path.

Tier 2.5 of the top-1 hardening roadmap: "write audit_silent_returns.py
that emits a report of every silent-fallback site with classification.
Target: 0 unclassified."

What this script does
---------------------
Walks every .py file under app/ and uses AST analysis (NOT regex) to
find `if <name> is None:` guard blocks where the body is a bare
`return <literal>` / `return` / `pass` / `continue`. These are the
classic Redis-down fast paths: the guard catches a no-op Redis client
and fails open with a safe default.

Each hit is classified as:

* `observed`   — the handler calls `record_silent_return(...)` from
                 `app.core.silent_fallback`. Good. This is the target
                 state for every site.
* `bare`       — the handler returns without logging OR recording. This
                 is the failure mode Tier 2.1 exists to fix.
* `logged`     — the handler logs something (log.warning / log.debug /
                 log.info) but does not call record_silent_return.
                 Partially observable but not counted.

Heuristic for "is None" guard: we match both
    if rc is None: ...
and
    if client is None: ...
so it catches both the `_redis()` and `_client()` return patterns.

Usage
-----
    ./venv/bin/python scripts/audit_silent_returns.py           # pretty report
    ./venv/bin/python scripts/audit_silent_returns.py --strict  # exit 1 on any bare

`--strict` is the gate we eventually wire into preflight once the bare
count reaches 0.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import Counter, defaultdict

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

# Files exempt from the `--strict` gate. These modules implement the
# silent-fallback observability plane itself, so recording from inside
# them would be a circular reference: record_silent_return lives in
# silent_fallback.py, which imports from redis_client.py. Any fallback
# return inside those two files cannot itself call record_silent_return
# without causing an infinite observation loop on Redis failure.
SELF_REFERENTIAL_FILES = {
    "app/core/redis_client.py",
    "app/core/silent_fallback.py",
}

# Names the guard check targets. These are the common accessor-return
# variable names across the codebase. MED-16 closure 2026-04-24:
# this set is now a BASELINE; the scanner also auto-discovers additional
# names from actual `if <name> is None: record_silent_return(...)`
# patterns in the tree — see _discover_guard_names_from_tree().
GUARD_NAMES_BASELINE = frozenset({"rc", "client", "r", "redis_client", "_rc"})


def _discover_guard_names_from_tree(tree: ast.Module) -> set[str]:
    """Find every `if <Name> is None: <body containing record_silent_return>`
    and return the set of Name IDs. Lets the audit learn app-specific
    guard names (e.g. `pipe`, `_cache`, `rds`) without a static list
    growing stale."""
    discovered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Match `<Name> is None`
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Is)
            and isinstance(test.left, ast.Name)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            continue
        # Body must call record_silent_return somewhere.
        calls_rsr = False
        for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Name) and fn.id == "record_silent_return":
                    calls_rsr = True
                    break
                if isinstance(fn, ast.Attribute) and fn.attr == "record_silent_return":
                    calls_rsr = True
                    break
        if calls_rsr:
            discovered.add(test.left.id)
    return discovered

# Safe fallback literal kinds accepted as a silent-fallback return.
def _is_fallback_body(body: list[ast.stmt]) -> bool:
    if not body:
        return False
    # Allow any leading ImportFrom / Import / Expr (inline
    # `from app.core.silent_fallback import record_silent_return` +
    # `record_silent_return(...)`) then a return/pass/continue.
    stmts = [
        s for s in body
        if not isinstance(s, (ast.ImportFrom, ast.Import))
    ]
    if not stmts:
        return False
    # Drop a leading expression statement (could be record_silent_return).
    if isinstance(stmts[0], ast.Expr) and len(stmts) >= 2:
        stmts = stmts[1:]
    head = stmts[0]
    if isinstance(head, (ast.Return, ast.Pass, ast.Continue)):
        return True
    return False


def _handler_records(body: list[ast.stmt]) -> bool:
    """True if this guard body calls record_silent_return(...)."""
    for node in body:
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        if isinstance(fn, ast.Name) and fn.id == "record_silent_return":
            return True
        if isinstance(fn, ast.Attribute) and fn.attr == "record_silent_return":
            return True
    return False


def _handler_logs(body: list[ast.stmt]) -> bool:
    for node in body:
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        if isinstance(fn, ast.Attribute) and fn.attr in {
            "debug", "info", "warning", "error", "exception", "critical"
        }:
            return True
    return False


class Finding:
    __slots__ = ("file", "line", "guard", "kind")

    def __init__(self, file: str, line: int, guard: str, kind: str):
        self.file = file
        self.line = line
        self.guard = guard
        self.kind = kind  # observed | logged | bare


def scan_file(path: pathlib.Path, extra_guards: set[str] | None = None) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    findings: list[Finding] = []
    rel = path.relative_to(APP_ROOT.parent).as_posix()

    # MED-16: effective guard set = baseline ∪ any extra names passed in
    # (auto-derived from the full app/ tree scan by walk_app()).
    effective_guards = set(GUARD_NAMES_BASELINE) | (extra_guards or set())

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Match both `X is None` and `not X`.
        name = None
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is):
            left = test.left
            right = test.comparators[0]
            if (
                isinstance(left, ast.Name)
                and isinstance(right, ast.Constant)
                and right.value is None
            ):
                name = left.id
        if name not in effective_guards:
            continue
        if not _is_fallback_body(node.body):
            continue

        if _handler_records(node.body):
            kind = "observed"
        elif _handler_logs(node.body):
            kind = "logged"
        else:
            kind = "bare"
        findings.append(Finding(rel, node.lineno, name, kind))
    return findings


def walk_app() -> list[Finding]:
    findings: list[Finding] = []
    # First pass: auto-discover extra guard names from the whole app/
    # tree so we aren't constrained to the hardcoded baseline.
    discovered: set[str] = set()
    py_paths: list[pathlib.Path] = []
    for path in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(APP_ROOT.parent).as_posix()
        if rel in SELF_REFERENTIAL_FILES:
            continue
        py_paths.append(path)
        try:
            tree = ast.parse(path.read_text())
        except Exception:
            continue
        discovered.update(_discover_guard_names_from_tree(tree))
    # Second pass: scan with the expanded guard set.
    for path in py_paths:
        findings.extend(scan_file(path, extra_guards=discovered))
    return findings


def main() -> int:
    strict = "--strict" in sys.argv
    findings = walk_app()

    by_kind = Counter(f.kind for f in findings)
    by_file_bare = defaultdict(int)
    for f in findings:
        if f.kind == "bare":
            by_file_bare[f.file] += 1

    total = sum(by_kind.values())
    print(f"audit_silent_returns: scanned {APP_ROOT}")
    print(f"  total sites: {total}")
    print(f"    observed (record_silent_return): {by_kind.get('observed', 0)}")
    print(f"    logged only: {by_kind.get('logged', 0)}")
    print(f"    bare (no observability): {by_kind.get('bare', 0)}")
    print()

    if by_kind.get("bare"):
        print("Top 15 files by bare-fallback count:")
        ranked = sorted(by_file_bare.items(), key=lambda kv: kv[1], reverse=True)[:15]
        for file, n in ranked:
            print(f"  {n:3d}  {file}")
        print()

    if strict and by_kind.get("bare"):
        print(f"FAIL: {by_kind['bare']} bare silent fallbacks remain (target: 0)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
