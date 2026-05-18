#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of app/api/track.py source — code structure, not
#   runtime state. Commit-stage-only (like audit_dashboard_lazy_db).
"""audit_track_lazy_db.py — structural preventer (jewel J3 follow-on,
honest-residual #6).

`/track` is the highest-VOLUME path on the system. Post J3-part-2 the
dominant traffic (non-purchase analytics) is Redis-only on the hot
path — `_is_known_shop` short-circuits on a cache hit BEFORE any
db.query, then the event is enqueued to the ingest buffer. FastAPI
resolves Depends() BEFORE the handler body, so an eager
`Depends(get_db)` pins a primary PgBouncer connection (and runs 2 SET
LOCAL) for the WHOLE request even on that 0-DB buffered path — the
exact c≈64 conn-pin cliff class, on the busiest write path, behind the
shared 150-conn ceiling.

Why a DEDICATED preventer (not just audit_cachefirst_conn_pin.py): the
generic class audit is AST-LOCAL — it only flags a handler whose own
body contains a `cache_get(` short-circuit. `/track`'s cache_get is
one level down inside `_is_known_shop(db, ...)`, invisible to the
generic heuristic, so it would never flag `/track`. This audit pins
the contract positively instead: the two `/track` route handlers MUST
take their db via `Depends(get_lazy_db)` (the lazy WRITE session that
stays 0-conn until first real DB use), never the eager
`Depends(get_db)` / `Depends(get_read_db)`.

Non-vacuous: it flags the exact pre-fix shape (`Depends(get_db)`) and
is GREEN only on the lazy-wired tree. A textual
`# lazy-db: ok — <reason>` marker on the def line opts a handler out.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "api" / "track.py"
# The two ingest route handlers that must use the lazy write session.
_GUARDED = {"track_event", "track_event_batch"}
_EAGER_DEPS = {"get_db", "get_read_db"}
_LAZY_DEP = "get_lazy_db"


def _depends_dep_name(func: ast.FunctionDef) -> str | None:
    """Return the dependency callable name passed to a Depends() default
    of any param, else None."""
    for default in func.args.defaults + func.args.kw_defaults:
        if default is None:
            continue
        if (
            isinstance(default, ast.Call)
            and getattr(default.func, "id", None) == "Depends"
            and default.args
            and getattr(default.args[0], "id", None) is not None
        ):
            return default.args[0].id
    return None


def _is_route(func: ast.FunctionDef) -> bool:
    for dec in func.decorator_list:
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
    found: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in _GUARDED or not _is_route(node):
            continue
        found.add(node.name)
        window = "\n".join(lines[max(0, node.lineno - 3):node.lineno])
        if "lazy-db: ok" in window:
            continue
        dep = _depends_dep_name(node)
        if dep in _EAGER_DEPS:
            violations.append(
                f"  app/api/track.py:{node.lineno} {node.name}() — "
                f"Depends({dep}) pins a primary pooled connection for "
                f"the whole request, but the dominant non-purchase path "
                f"is Redis-only (0 DB). Use Depends({_LAZY_DEP}) (lazy "
                f"WRITE session) so the buffered path stays 0-conn."
            )
        elif dep != _LAZY_DEP:
            violations.append(
                f"  app/api/track.py:{node.lineno} {node.name}() — db "
                f"dependency is Depends({dep}); expected "
                f"Depends({_LAZY_DEP}) (the highest-volume path must "
                f"not pin a pooled connection on the buffered branch)."
            )

    missing = sorted(_GUARDED - found)
    if missing:
        # The handlers were renamed/removed — the contract can no longer
        # be asserted. Fail loud rather than pass vacuously.
        violations.append(
            "  expected /track route handlers not found in "
            f"app/api/track.py: {', '.join(missing)} (rename? "
            "update _GUARDED + re-verify the lazy-db contract)."
        )

    if violations:
        print("audit_track_lazy_db: FAIL — the highest-volume write "
              "path pins a pooled connection (the c≈64 cliff class):")
        print("\n".join(violations))
        return 1
    print("audit_track_lazy_db: OK — /track + /track/batch use "
          "Depends(get_lazy_db); buffered path holds 0 connections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
