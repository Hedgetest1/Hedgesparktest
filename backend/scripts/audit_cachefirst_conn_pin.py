#!/usr/bin/env python
# invariant-eligible: false
#   Static AST scan of app/api/*.py source — code structure, not
#   runtime state. Commit-stage-only (like audit_dashboard_lazy_db).
"""audit_cachefirst_conn_pin.py — class-wide blast-radius map.

Born 2026-05-15b after the /dashboard/overview pool-timeout cliff:
a route handler that declares `Depends(get_db|get_read_db)` AND
short-circuits on a Redis `cache_get` hit pins a pooled PgBouncer
connection for the WHOLE request even when the warm path does zero
DB work. FastAPI resolves Depends BEFORE the handler body, so the
connection is checked out before the cache check can return. Behind
PgBouncer's GLOBAL ceiling (shared by all uvicorn workers) this
cliffs under concurrency (proven c≈64 → 100% err / pool_timeout=30).

The dashboard fix (commit 8291d0d) acquired the session LAZILY
(only inside the cold-build path). This audit finds EVERY OTHER
handler with the identical structural bug so the macchia-d'olio
sweep targets the real set, not a guess.

Classification per route handler in app/api/*.py:
  RED   — has Depends(get_db|get_read_db) AND is cache-first
          (a `cache_get(` whose value is returned before the db is
          used). IDENTICAL bug → lazy-DB fix applies directly.
  YELLOW— has Depends(get_db|get_read_db), NO cache-first short
          circuit. Legitimately uses db every request; the residual
          risk is query DURATION under the shared ceiling, a
          different remediation (query budget / conn scoping) —
          reported for awareness, NOT a lazy-DB target.
  (skip) — no Depends db param.

Opt-out: `# lazy-db: ok — <reason>` within 3 lines above the def.

Exit non-zero if any RED remains (preflight gate once swept);
`--report` prints the full RED+YELLOW inventory and exits 0.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe glob read

API_DIR = Path(__file__).resolve().parent.parent / "app" / "api"
_DEP_NAMES = {"get_db", "get_read_db"}


def _depends_db_param(fn: ast.FunctionDef) -> str | None:
    for d in fn.args.defaults + fn.args.kw_defaults:
        if d is None:
            continue
        if (
            isinstance(d, ast.Call)
            and getattr(d.func, "id", None) == "Depends"
            and d.args
            and getattr(d.args[0], "id", None) in _DEP_NAMES
        ):
            return d.args[0].id
    return None


def _is_route(fn: ast.FunctionDef) -> bool:
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and getattr(dec.func.value, "id", None) == "router"
            and dec.func.attr in ("get", "post", "patch", "delete", "put")
        ):
            return True
    return False


def _cache_first(fn: ast.FunctionDef) -> bool:
    """Heuristic matching the proven dashboard shape: a `cache_get(`
    call assigned to a name, followed within a few statements by an
    `if <name> ... : return <name>` BEFORE the db param is used. We
    approximate with: a top-level `cache_get(` exists AND a `return`
    of that cached name occurs at a statement index lower than the
    first reference to the db parameter. Conservative: if cache_get
    exists and ANY return precedes the first db-name use, flag RED."""
    db_param = None
    for a in fn.args.args + fn.args.kwonlyargs:
        if a.arg in ("db", "_db", "session", "read_db"):
            db_param = a.arg
            break

    body = fn.body
    first_cache_get_idx = None
    first_db_use_idx = None
    first_return_after_cache_idx = None

    for i, stmt in enumerate(body):
        calls = [n for n in ast.walk(stmt) if isinstance(n, ast.Call)]
        has_cache_get = any(
            getattr(c.func, "id", None) == "cache_get"
            or getattr(c.func, "attr", None) == "cache_get"
            for c in calls
        )
        if has_cache_get and first_cache_get_idx is None:
            first_cache_get_idx = i

        if db_param is not None and first_db_use_idx is None:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Name) and n.id == db_param:
                    # ignore the param appearing in its own default
                    first_db_use_idx = i
                    break

        if (
            first_cache_get_idx is not None
            and i >= first_cache_get_idx
            and first_return_after_cache_idx is None
        ):
            for n in ast.walk(stmt):
                if isinstance(n, ast.Return) and n.value is not None:
                    first_return_after_cache_idx = i
                    break

    if first_cache_get_idx is None:
        return False
    if first_return_after_cache_idx is None:
        return False
    # cache-first iff a return fires at/after the cache_get and BEFORE
    # the db param is ever touched (db unused on the warm path).
    if first_db_use_idx is None:
        return True
    return first_return_after_cache_idx <= first_db_use_idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="print full inventory + exit 0 (triage mode)")
    args = ap.parse_args()

    red: list[str] = []
    yellow: list[str] = []

    for path in sorted(API_DIR.glob("*.py")):
        src = safe_read_text(path)  # TOCTOU-safe (audit_audit_io_safety)
        if src is None:
            continue
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not _is_route(node):
                continue
            window = "\n".join(lines[max(0, node.lineno - 4):node.lineno])
            if "lazy-db: ok" in window:
                continue
            dep = _depends_db_param(node)
            if not dep:
                continue
            tag = f"app/api/{path.name}:{node.lineno} {node.name}() Depends({dep})"
            if _cache_first(node):
                red.append(tag)
            else:
                yellow.append(tag)

    print(f"audit_cachefirst_conn_pin: {len(red)} RED (cache-first, "
          f"identical c=64 bug) / {len(yellow)} YELLOW (uncached, "
          f"shared-ceiling duration risk)")
    if args.report or red:
        if red:
            print("\n🔴 RED — lazy-DB sweep targets (identical bug):")
            for r in red:
                print(f"  {r}")
        if args.report and yellow:
            print(f"\n🟡 YELLOW — {len(yellow)} uncached Depends-db handlers "
                  f"(awareness only, not lazy-DB targets):")
            for y in yellow[:60]:
                print(f"  {y}")
            if len(yellow) > 60:
                print(f"  … +{len(yellow) - 60} more")
    if args.report:
        return 0
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
