"""
audit_redis_client_imports.py — Structural preventer for the class of
"silent fail-open" bugs where a module imports a name from
app.core.redis_client that does not exist.

Background
----------
On 2026-04-18 two live bugs were traced to the same pattern:

  1. app/api/frontend_errors.py imported `get_redis` — a function that
     has NEVER existed. Every call raised ImportError, was swallowed by
     the outer `except Exception: return True (fail-open)`, and the
     30-reports/min/IP rate limit silently did nothing for weeks.
  2. app/workers/segment_monitor_worker.py imported `redis_client` —
     a module-level attribute that also does not exist. `_load_cursor`
     and `_save_cursor` both silently returned 0, meaning the segment
     monitor's round-robin cursor never persisted. Harmless with 2
     merchants; at 10k it would starve the tail of the shop list.

Both bugs hid inside `try/except Exception` blocks that are otherwise
good defensive practice. Code review alone doesn't catch them because
the import statement looks plausible.

What this script does
---------------------
Parses the current `app.core.redis_client` module to build an allowlist
of public names actually exported. Then walks the entire `app/` tree
and every `from app.core.redis_client import NAME` statement is
checked against that allowlist. Any import of a name that doesn't
exist is reported and the script exits non-zero in --strict mode.

Usage
-----
  ./venv/bin/python scripts/audit_redis_client_imports.py            # list
  ./venv/bin/python scripts/audit_redis_client_imports.py --strict   # exit 1 on any bad import

Scope is narrowly the redis_client module — the bug class we've
actually seen — rather than a universal unused-import linter. Small,
targeted, fast.
"""
from __future__ import annotations

import ast
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT / "app"
REDIS_MODULE = APP_ROOT / "core" / "redis_client.py"


def _allowed_names() -> set[str]:
    """Parse redis_client.py and return every top-level def/assign name.
    We include underscored names too (like `_client`) since callers
    legitimately import them across the codebase.

    2026-04-23 retro DA: also picks up walrus-assigned names at module
    scope (`NAME := value`), not just plain assignments. Python doesn't
    permit `NAME := x` at module top-level outside expression context,
    so this is a defensive addition against future Python versions
    that may allow it. Also handles ast.AugAssign (`x += y` at module
    level) and tuple-unpack assignments like `A, B = 1, 2`.
    """
    tree = ast.parse(REDIS_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            # Handle tuple unpacking: `A, B = f()` binds both.
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Re-exports. For `import X as Y`, the local binding is Y;
            # for `import X`, the local binding is X. Track the local
            # binding — that's what callers actually reference.
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    # Walrus-assigned bindings at module scope (defensive for future
    # Python versions that may permit this at top-level).
    for node in ast.walk(tree):
        if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _find_bad_imports(allowed: set[str]) -> list[tuple[pathlib.Path, int, str]]:
    bad: list[tuple[pathlib.Path, int, str]] = []
    for py_file in APP_ROOT.rglob("*.py"):
        if py_file.samefile(REDIS_MODULE):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "app.core.redis_client":
                continue
            for alias in node.names:
                if alias.name == "*":
                    # Defer to runtime — `from ... import *` will just grab __all__.
                    continue
                if alias.name not in allowed:
                    bad.append((py_file, node.lineno, alias.name))
    return bad


def main() -> int:
    strict = "--strict" in sys.argv
    allowed = _allowed_names()
    print(
        "audit_redis_client_imports: "
        f"{len(allowed)} names exported by app.core.redis_client"
    )
    bad = _find_bad_imports(allowed)
    if not bad:
        print("  ✓ every imported name exists in redis_client.py")
        return 0
    print(f"\nFAIL: {len(bad)} import(s) reference non-existent names:")
    for path, line, name in bad:
        rel = path.relative_to(BACKEND_ROOT)
        print(f"  {rel}:{line}  imports `{name}`  (not in redis_client.py)")
    print(
        "\nHint: the allowed names are: "
        + ", ".join(sorted(allowed))
    )
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main())
