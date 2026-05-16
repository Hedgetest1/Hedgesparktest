#!/usr/bin/env python
# invariant-eligible: false
#   Static AST check of database.py / deps.py / conftest.py source —
#   code structure, not runtime state. Commit-stage-only by nature.
"""audit_conftest_db_override_coverage.py — structural preventer.

Born 2026-05-16 (ledger #15). On 2026-05-15b a new session-yielding
FastAPI dependency (`get_lazy_read_db`) was added to database.py but
NOT to tests/conftest.py's `dependency_overrides` map — it opened a
REAL `ReadSession`, escaping the SAVEPOINT hermetic transaction and
breaking 18 tests. It was fixed for that dep, but nothing stops the
NEXT new session dep from silently re-introducing the gap.

The contract: every module-level dependency in app/core/database.py
(or app/core/deps.py) that `yield`s a SQLAlchemy session (constructs
`SessionLocal()` / `ReadSession()` / `_LazyReadSession()`) MUST be
registered in `tests/conftest.py` `fastapi_app.dependency_overrides`,
so tests stay hermetic against the live Postgres test DB.

FAIL (exit 1) if a session-yielding dep is not overridden in conftest.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe read

_ROOT = Path(__file__).resolve().parent.parent
_SOURCES = [
    _ROOT / "app" / "core" / "database.py",
    _ROOT / "app" / "core" / "deps.py",
]
_CONFTEST = _ROOT / "tests" / "conftest.py"
_SESSION_CTORS = ("SessionLocal(", "ReadSession(", "_LazyReadSession(")


def _session_yielding_deps(path: Path) -> list[str]:
    """Top-level functions that yield a freshly-constructed session."""
    out: list[str] = []
    src = safe_read_text(path)
    if src is None:
        return out
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_src = ast.get_source_segment(src, node) or ""
        has_yield = any(
            isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node)
        )
        if has_yield and any(c in body_src for c in _SESSION_CTORS):
            out.append(node.name)
    return out


def main() -> int:
    conftest_src = safe_read_text(_CONFTEST)
    if conftest_src is None:
        print("audit_conftest_db_override_coverage: SKIP — no conftest.")
        return 0

    deps: list[str] = []
    for s in _SOURCES:
        if s.exists():
            deps.extend(_session_yielding_deps(s))

    missing = [
        d for d in deps
        if not re.search(rf"dependency_overrides\[\s*{d}\s*\]", conftest_src)
    ]
    if missing:
        print("audit_conftest_db_override_coverage: FAIL — session-"
              "yielding dep(s) not overridden in tests/conftest.py "
              "(test-hermeticity escape, ledger #15):")
        for d in missing:
            print(f"  {d}  — add "
                  f"fastapi_app.dependency_overrides[{d}] = _override_get_db")
        return 1
    print(f"audit_conftest_db_override_coverage: OK — all "
          f"{len(deps)} session-yielding deps overridden ({', '.join(deps)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
