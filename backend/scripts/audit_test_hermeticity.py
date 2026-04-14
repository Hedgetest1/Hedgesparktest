#!/usr/bin/env python
"""
audit_test_hermeticity.py — Flag tests that are likely to leak against
the shared production DB under PostgreSQL READ COMMITTED isolation.

Background
----------
backend tests run inside a SAVEPOINT rolled back after each test
(see tests/conftest.py). The SAVEPOINT isolates INSERTs/UPDATEs from
prod, but READ COMMITTED lets the test SEE committed rows from other
sessions — so a test that asserts an empty/sparse state by querying
a shared table (`assert db.query(X).first() is None`, `assert count
== 0`, etc.) can break the moment the production workers write a
real row that matches the query.

This script flags test functions that look like they might suffer
from this pattern:
  * Contain at least one negative-state assertion on a query result
    (`is None`, `== None`, `== 0`, `is False` after a query)
  * AND query a shared table (db.query(Model).first/count/scalar/one_or_none)
  * AND do NOT call `.delete(` on that same model first (the usual
    hermeticity reset pattern)

It is heuristic — false positives are expected (a test might reset
state via a fixture, or use a unique-per-test filter). The goal is
to surface candidates for review, not to block commits.

Exit code:
  0 — no suspicious patterns found
  2 — informational report (never blocks; wire into preflight as
      a warning, not a failure)

TIER 0 safe — read-only static analysis.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from dataclasses import dataclass

TESTS_DIR = pathlib.Path("/opt/wishspark/backend/tests")


@dataclass
class Suspicion:
    file: str
    function: str
    line: int
    model: str
    reason: str


_NEGATIVE_STATE_ATTRS = {"first", "one_or_none", "scalar", "count"}


def _find_query_models(node: ast.AST) -> set[str]:
    """Walk `node` and collect model names appearing in `db.query(Model)`.

    A "query" is recognized as a call of form `db.query(Foo)` or
    `some_session.query(Foo)` at any depth.
    """
    models: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "query" and child.args:
                arg = child.args[0]
                if isinstance(arg, ast.Name):
                    models.add(arg.id)
    return models


def _has_delete_on_models(node: ast.AST, models: set[str]) -> bool:
    """True if the function body contains `.delete(` invoked as a
    method on a query that touches any of the given models."""
    if not models:
        return True  # nothing to protect
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr == "delete":
                # Walk back up to see if any of the target models appear
                # in the call chain. Simple heuristic: string-match the
                # model name in the unparsed call expression.
                src = ast.unparse(child)
                if any(m in src for m in models):
                    return True
    return False


def _has_negative_state_assertion(node: ast.AST) -> tuple[bool, str]:
    """True if the function body contains an assertion like
    `assert x is None` / `== None` / `== 0` / `is False` where `x`
    is plausibly a query result. `is not None` / `!= 0` / `is True`
    are POSITIVE assertions and do NOT trigger this detector."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Assert):
            continue
        test = child.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
            continue
        op = test.ops[0]
        right = test.comparators[0]
        # `is None` / `is False` — exclude `is not ...`
        if isinstance(op, ast.Is) and isinstance(right, ast.Constant):
            if right.value is None:
                return True, "assert ... is None"
            if right.value is False:
                return True, "assert ... is False"
        # `== 0` / `== None` — exclude `!= ...`
        if isinstance(op, ast.Eq) and isinstance(right, ast.Constant):
            if right.value == 0:
                return True, "assert ... == 0"
            if right.value is None:
                return True, "assert ... == None"
    return False, ""


def _calls_query_finalizer(node: ast.AST) -> bool:
    """True if the function body invokes a query finalizer like
    .first/.one_or_none/.scalar/.count on any call chain."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in _NEGATIVE_STATE_ATTRS:
                return True
    return False


def scan_file(path: pathlib.Path) -> list[Suspicion]:
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    out: list[Suspicion] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        if not _calls_query_finalizer(node):
            continue
        neg, reason = _has_negative_state_assertion(node)
        if not neg:
            continue
        models = _find_query_models(node)
        if not models:
            continue
        if _has_delete_on_models(node, models):
            continue
        out.append(Suspicion(
            file=str(path.relative_to(TESTS_DIR.parent)),
            function=node.name,
            line=node.lineno,
            model=",".join(sorted(models)),
            reason=reason,
        ))
    return out


def main() -> int:
    findings: list[Suspicion] = []
    for f in sorted(TESTS_DIR.glob("test_*.py")):
        findings.extend(scan_file(f))

    if not findings:
        print("✅ No suspicious negative-state-without-cleanup patterns found")
        return 0

    # Informational: informational exit code (2), never blocking.
    print(f"⚠️  {len(findings)} test functions flagged for hermeticity review:")
    print()
    for s in findings:
        print(f"  {s.file}:{s.line}  {s.function}")
        print(f"    model(s): {s.model}")
        print(f"    pattern:  {s.reason}  (no .delete() on model first)")
        print()
    print("These tests query a shared table and assert negative state without")
    print("resetting the table first inside the SAVEPOINT. If the production")
    print("worker writes a matching row during the test run, the assertion")
    print("will flip and the test will flake. Consider adding a reset call.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
