#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of app/api/dashboard.py source — code structure,
#   not runtime state. Commit-stage-only by nature (like
#   audit_sql_schema / audit_tenant_isolation); nothing for a 15-min
#   invariant_monitor cycle to observe.
"""audit_dashboard_lazy_db.py — structural preventer.

Born 2026-05-15 after the /dashboard/overview pool-timeout cliff: a
route handler that declares `db: ... = Depends(get_read_db)` (or
`get_db`) AND short-circuits on a Redis `cache_get` hit pins a pooled
PgBouncer connection for the ENTIRE request even though the warm path
issues zero queries. FastAPI resolves `Depends` BEFORE the handler
body, so the connection is checked out before the cache check can
return. At scale this wedges PgBouncer's global connection ceiling
(empirically: c≈64 → 100% errors at exactly pool_timeout=30s).

The contract: a cache-first dashboard handler must acquire its DB
session LAZILY (inside the cold-build path), never via `Depends`.

This audit fails (exit 1) if any `@router.(get|post)` handler in
app/api/dashboard.py has BOTH:
  - a parameter defaulting to Depends(get_db) / Depends(get_read_db)
  - a `cache_get(...)` call in its body
A textual `# lazy-db: ok — <reason>` marker on the def line opts a
specific handler out (for handlers that genuinely need request-scoped
db AND are not cache-first).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "api" / "dashboard.py"
_DEP_NAMES = {"get_db", "get_read_db"}


def _depends_db_param(func: ast.FunctionDef) -> str | None:
    """Return the offending dependency name if a param defaults to
    Depends(get_db|get_read_db), else None."""
    for default in func.args.defaults + func.args.kw_defaults:
        if default is None:
            continue
        # Depends(get_read_db)
        if (
            isinstance(default, ast.Call)
            and getattr(default.func, "id", None) == "Depends"
            and default.args
            and getattr(default.args[0], "id", None) in _DEP_NAMES
        ):
            return default.args[0].id
    return None


def _calls_cache_get(func: ast.FunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            fn = node.func
            if getattr(fn, "id", None) == "cache_get" or getattr(
                fn, "attr", None
            ) == "cache_get":
                return True
    return False


def _is_route(func: ast.FunctionDef) -> bool:
    for dec in func.decorator_list:
        # @router.get(...) / @router.post(...)
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and getattr(dec.func.value, "id", None) == "router"
            and dec.func.attr in ("get", "post")
        ):
            return True
    return False


def main() -> int:
    src = TARGET.read_text()
    lines = src.splitlines()
    tree = ast.parse(src)
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not _is_route(node):
            continue
        # opt-out marker on or just above the def line
        window = "\n".join(lines[max(0, node.lineno - 3):node.lineno])
        if "lazy-db: ok" in window:
            continue
        dep = _depends_db_param(node)
        if dep and _calls_cache_get(node):
            violations.append(
                f"  app/api/dashboard.py:{node.lineno} {node.name}() — "
                f"Depends({dep}) pins a DB connection for the whole "
                f"request but the handler is cache-first (calls "
                f"cache_get). Open the session lazily in the cold-build "
                f"path instead (see get_dashboard_overview)."
            )

    if violations:
        print("audit_dashboard_lazy_db: FAIL — connection pinned "
              "across a cache hit (the c=64 pool-timeout cliff class):")
        print("\n".join(violations))
        return 1
    print("audit_dashboard_lazy_db: OK — no cache-first dashboard "
          "handler pins a Depends(get_db/get_read_db) connection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
