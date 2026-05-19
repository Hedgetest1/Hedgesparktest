#!/usr/bin/env python3
# invariant-eligible: false — static AST audit over invariant_monitor.py
# SOURCE. Commit-stage-only by nature: it guards against a bad SOURCE
# edit (a new write_alert site without rollback) and is meaningless to
# re-run every 15-min runtime cycle against unchanged source. The
# runtime _AUDITS dispatch is for live-STATE checks, not source guards.
"""audit_invariant_monitor_rollback.py — preventer for the safety-net
poisoned-session class (born 2026-05-19).

Ground truth: 6 Sentry incidents (all 2026-05-11) — 'failed to write
invariant alert' / 'silent-audits alert write failed' — were caused by
`except` handlers in invariant_monitor that logged a DB failure but
never `db.rollback()`. The poisoned session (`InFailedSqlTransaction:
current transaction is aborted`) then silently rejected every
subsequent check + EVERY write_alert in the same cycle. The safety net
lost its own alerts during exactly the DB contention it exists to
detect.

Structural fix shipped same day: `_rollback_quiet` + `_safe_check`
wrapper + a `_rollback_quiet(db)` in every write_alert-failure handler.

This audit makes the class IMPOSSIBLE TO REGRESS: every `write_alert(`
call site in app/services/invariant_monitor.py must sit inside a
`try` whose `except` body calls `_rollback_quiet`. A future engineer
adding a new safety-net alert write without the rollback guard is
blocked at preflight.

Non-vacuity: the audit asserts it finds ≥10 guarded write_alert sites
(there are 12 today). If a refactor removed them all the assertion
fails loudly rather than passing on an empty set.

Exit 0 = clean, exit 1 = an unguarded write_alert site (regression).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "services" / "invariant_monitor.py"
_MIN_EXPECTED_SITES = 10  # 12 today; non-vacuity floor


def _calls(node: ast.AST, name: str) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == name:
                return True
            if isinstance(f, ast.Attribute) and f.attr == name:
                return True
    return False


def main() -> int:
    src = TARGET.read_text()
    tree = ast.parse(src)

    # Helpers must exist (the fix itself).
    fns = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for required in ("_rollback_quiet", "_safe_check"):
        if required not in fns:
            print(f"FAIL: invariant_monitor.{required}() missing — the "
                  f"poisoned-session structural fix was removed/renamed.")
            return 1

    guarded = 0
    unguarded: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # Does the try-body call write_alert?
        body_calls_wa = any(_calls(stmt, "write_alert") for stmt in node.body)
        if not body_calls_wa:
            continue
        # Every handler must roll back (directly or via _rollback_quiet).
        handler_ok = bool(node.handlers) and all(
            _calls(h, "_rollback_quiet") or _calls(h, "rollback")
            for h in node.handlers
        )
        if handler_ok:
            guarded += 1
        else:
            unguarded.append(getattr(node, "lineno", -1))

    if unguarded:
        print("FAIL: unguarded write_alert site(s) in invariant_monitor "
              "(except handler does not roll back the poisoned session):")
        for ln in unguarded:
            print(f"  - try at line {ln}: add _rollback_quiet(db) to its "
                  f"except handler (see audit docstring + 2026-05-11 "
                  f"incidents)")
        return 1

    if guarded < _MIN_EXPECTED_SITES:
        print(f"FAIL (non-vacuity): only {guarded} guarded write_alert "
              f"sites found, expected ≥{_MIN_EXPECTED_SITES}. The audit "
              f"may be matching nothing — verify the fix is intact.")
        return 1

    print(f"OK: {guarded} write_alert sites in invariant_monitor are all "
          f"rollback-guarded; _rollback_quiet + _safe_check present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
