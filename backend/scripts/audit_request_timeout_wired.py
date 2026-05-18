#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of app/core/database.py — code structure, not
#   runtime state. Commit-stage-only (like audit_dashboard_lazy_db).
"""audit_request_timeout_wired.py — preventer.

Born 2026-05-15b. PG statement_timeout / idle_in_transaction are 0
(unbounded) everywhere; the ONLY bound is the per-request
`_apply_request_timeouts()` SET LOCAL wired into the 3 request DB
dependencies. If a refactor drops that call from any of them, an
unbounded request query can again starve the shared PgBouncer pool
for every endpoint (the 284-handler contention class reopens).

This audit fails (exit 1) unless ALL of:
  - get_db
  - get_read_db
  - EVERY proxy `_ensure` (now two: _LazyReadSession AND _LazyDbSession
    — the write-side sibling added with the lazy /track fix; a single
    unbound proxy reopens the contention class, so the check is AND
    over all `_ensure` defs, not "some _ensure binds it").
in app/core/database.py contain a call to `_apply_request_timeouts`.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "core" / "database.py"
_REQUIRED = {"get_db", "get_read_db", "_ensure"}


def _calls_apply(fn: ast.FunctionDef) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if getattr(f, "id", None) == "_apply_request_timeouts" or \
               getattr(f, "attr", None) == "_apply_request_timeouts":
                return True
    return False


def main() -> int:
    tree = ast.parse(TARGET.read_text())
    seen: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _REQUIRED:
            ok = _calls_apply(node)
            # `_ensure` now has TWO defs (_LazyReadSession AND
            # _LazyDbSession). AND over all occurrences: every proxy
            # must bind the timeout — a single unbound `_ensure` would
            # otherwise pass on a lucky walk order while reopening the
            # unbounded-pool-hold class for that proxy's call sites.
            seen[node.name] = ok if node.name not in seen else (seen[node.name] and ok)

    missing = sorted(_REQUIRED - {k for k, v in seen.items() if v})
    if missing:
        print("audit_request_timeout_wired: FAIL — the per-request "
              "connection-hold bound was dropped from: "
              + ", ".join(missing)
              + ". Unbounded request queries can starve the shared "
              "PgBouncer pool (the 284-handler contention class). "
              "Re-add _apply_request_timeouts() to each.")
        return 1
    print("audit_request_timeout_wired: OK — get_db / get_read_db / "
          "every proxy _ensure (_LazyReadSession + _LazyDbSession) "
          "bound by _apply_request_timeouts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
