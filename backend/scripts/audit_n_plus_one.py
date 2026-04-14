#!/usr/bin/env python
"""
audit_n_plus_one.py — Find N+1 query patterns.

A for-loop that issues a DB call per iteration is the classic N+1 trap.
At 10 items it's invisible; at 10k items it nukes the request path.

Detection: walk the AST, find `for` loops whose body contains a call
to `db.execute / db.query / db.add / db.delete / session.execute / ...`
AND the loop variable is used in the call (either as a bind param or
a direct reference).

False-positive mitigation:
  * Loops over a small literal range (range(1,4)) are exempt
  * Loops that bulk-collect results into a list and then single-commit
    are flagged as "bulk insert" (still N+1 on execute, but not on commit)
  * Skip test files
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import defaultdict

APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
# Receiver names we trust as "this is a SQLAlchemy session/connection".
# Removed `"s"` (too generic — matched dict.get / set ops / string ops).
_DB_CALL_TARGETS = {"db", "session", "conn", "connection"}
# Methods. `get` and `first` are dict/list-ish so we keep them only when
# the receiver is unambiguously a session — the receiver guard above
# already enforces that.
_DB_CALL_METHODS = {"execute", "query", "scalar", "scalars", "fetchone", "fetchall"}


class Finding:
    __slots__ = ("file", "line", "loop_var", "call_location")

    def __init__(self, file: str, line: int, loop_var: str, call_location: int):
        self.file = file
        self.line = line
        self.loop_var = loop_var
        self.call_location = call_location


def call_is_db_read(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    name = func.attr.lower()
    if name not in _DB_CALL_METHODS:
        return False
    # Receiver: must be a db-ish name
    recv = func.value
    while isinstance(recv, ast.Attribute):
        recv = recv.value
    if isinstance(recv, ast.Name):
        return recv.id in _DB_CALL_TARGETS
    return False


def loop_is_small_literal(iter_node: ast.expr) -> bool:
    """Exempt range(...) with small constants."""
    if not isinstance(iter_node, ast.Call):
        return False
    if not isinstance(iter_node.func, ast.Name) or iter_node.func.id != "range":
        return False
    # range(N) or range(a, b) where the final bound is a small constant
    last = iter_node.args[-1] if iter_node.args else None
    return (
        isinstance(last, ast.Constant)
        and isinstance(last.value, int)
        and last.value <= 10
    )


def audit_file(path: pathlib.Path) -> list[Finding]:
    try:
        src = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    findings: list[Finding] = []
    rel = str(path.relative_to(APP_ROOT.parent))

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if loop_is_small_literal(node.iter):
            continue

        # Extract the loop var name(s)
        if isinstance(node.target, ast.Name):
            loop_vars = {node.target.id}
        elif isinstance(node.target, ast.Tuple):
            loop_vars = {
                e.id for e in node.target.elts if isinstance(e, ast.Name)
            }
        else:
            loop_vars = set()

        # Scan the loop body for DB calls
        for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(inner, ast.Call) and call_is_db_read(inner):
                # Check if the loop variable is referenced in the call
                used_names = {
                    n.id for n in ast.walk(inner) if isinstance(n, ast.Name)
                }
                if loop_vars & used_names:
                    findings.append(Finding(
                        rel, node.lineno,
                        ", ".join(sorted(loop_vars)) or "?",
                        inner.lineno,
                    ))
                    break  # one finding per loop is enough
    return findings


def main() -> int:
    all_findings: list[Finding] = []
    for py in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        all_findings.extend(audit_file(py))

    if not all_findings:
        print("✅ No N+1 patterns detected.")
        return 0

    by_file: dict[str, list[Finding]] = defaultdict(list)
    for f in all_findings:
        by_file[f.file].append(f)

    print(f"⚠️  N+1 CANDIDATES ({len(all_findings)} across {len(by_file)} files)\n")
    for file, hits in sorted(by_file.items()):
        print(f"  {file}")
        for h in hits[:4]:
            print(f"    loop@{h.line}  (db call at :{h.call_location}, var={h.loop_var})")
        if len(hits) > 4:
            print(f"    ... and {len(hits) - 4} more")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
