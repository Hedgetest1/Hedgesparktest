#!/usr/bin/env python3
"""audit_audit_io_safety.py — preventer for TOCTOU regressions in
preflight scripts that walk the source tree.

Problem class
-------------
Scripts that walk the source tree with `Path.rglob(...)` / `Path.glob(...)`
followed by `path.read_text(...)` (or `path.open(...)`) are vulnerable to
a classic TOCTOU race when a concurrent test fixture creates+deletes a
file inside the scanned tree:

    test_audit_data_truth_gate writes _test_hardcoded_eur_DELETE_ME.py
    under app/services/, deletes at teardown.
        → invariant_monitor cycle running in parallel
        → rglob discovers the file
        → read_text() raises FileNotFoundError
        → audit exits non-zero
        → invariant_regression CRITICAL fired

The same race fired twice on 2026-05-13 (audit_cte_missing_comma,
audit_tier_cost_literals). 75+ other audits had the latent bug — they
just hadn't lost the timing roulette yet.

The defense (AST-precise USE-SITE check)
-----------------------------------------
**Born 2026-05-14 v1**: import-presence check (`from _audit_io import
safe_read_text` OR explicit `try/except (FileNotFoundError,
PermissionError)` mention). Independent close audit caught the gap:
import-without-use bypassed the check (concrete victim:
`audit_test_hermeticity.py` imported the helper, kept raw `read_text`
call site → preventer said clean → bug latent).

**v2 (this file)**: AST-walk every `Call` node where the function is
`<receiver>.read_text(...)`, `<receiver>.read_bytes(...)`, or
`<receiver>.open(...)`. For each such call, check:

    (a) Is the receiver a name bound to a `glob`/`rglob` iterator
        in any enclosing `for` loop? OR
    (b) Is the receiver an arg of a function called from such a loop?

If yes (call site is in a path-iterator scope), require coverage:

    - The call is `safe_read_text(<receiver>)` itself (canonical), OR
    - The call is enclosed in a `try / except` whose handler names
      `FileNotFoundError` or `PermissionError` (escape-valve pattern).

Scope is BOTH `scripts/audit_*.py` and other `scripts/*.py` that walk
the source tree (e.g. `session_telemetry_harvester.py`,
`suggest_test_exempts.py` — both surfaced as victims by the same audit).
The preventer self-excludes (no point flagging itself) and excludes
the helper file `_audit_io.py`.

Exit codes
----------
    0 — every call site covered
    1 — one or more vulnerable call sites
    2 — script error (e.g. malformed input file)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from _audit_io import safe_read_text  # noqa: E402

# Files in `scripts/` that don't need to be scanned. Keep this TINY
# and document each entry — exemptions accrete drift.
_EXEMPT: frozenset[str] = frozenset({
    "audit_audit_io_safety.py",  # this file (would self-flag)
    "_audit_io.py",              # the helper itself (no scanning logic)
    "_audit_telemetry_shim.py",  # telemetry stub, no rglob
})

_HELPER_NAME = "safe_read_text"
_RACE_RAISERS: frozenset[str] = frozenset({"FileNotFoundError", "PermissionError"})
_READ_METHODS: frozenset[str] = frozenset({"read_text", "read_bytes", "open"})
_GLOB_METHODS: frozenset[str] = frozenset({"glob", "rglob"})


def _names_bound_by_glob_loops(tree: ast.Module) -> set[str]:
    """Return every variable name bound by a `for X in <obj>.glob(...)`
    or `<obj>.rglob(...)` loop anywhere in the module.

    Includes both direct `for X in` and `for X in sorted(...)` /
    `for X in <iter_func>(<obj>.rglob(...))` patterns — we walk the
    full iterator expression and check if any sub-call is a glob.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # Does the iterator expression contain a .glob/.rglob call?
        contains_glob = any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in _GLOB_METHODS
            for sub in ast.walk(node.iter)
        )
        if not contains_glob:
            continue
        # Bind every name in the for-target tuple/name
        for tgt in ast.walk(node.target):
            if isinstance(tgt, ast.Name):
                bound.add(tgt.id)
    return bound


def _is_in_race_handler(node: ast.AST, ancestors: dict[int, ast.AST]) -> bool:
    """Return True iff node is lexically inside a `try` whose `except`
    names FileNotFoundError or PermissionError (or a tuple containing
    one). Walks ancestors via the supplied parent map."""
    cur = ancestors.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Try):
            for handler in cur.handlers:
                exc = handler.type
                if exc is None:
                    continue
                exc_names: list[str] = []
                if isinstance(exc, ast.Name):
                    exc_names.append(exc.id)
                elif isinstance(exc, ast.Tuple):
                    for elt in exc.elts:
                        if isinstance(elt, ast.Name):
                            exc_names.append(elt.id)
                if any(name in _RACE_RAISERS for name in exc_names):
                    return True
        cur = ancestors.get(id(cur))
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _path_typed_function_params(tree: ast.Module) -> set[str]:
    """Return parameter names of any function whose annotation is a
    `Path` / `pathlib.Path`. Born 2026-05-14 v3 sharpening: a function
    like `def scan_file(path: Path)` called from `for py in rglob(...):
    scan_file(py)` exhibits the same race — the parameter `path` is
    a glob-yielded value across the call boundary. The v2 walker
    missed this because it tracked names only within their lexical
    binding scope; v3 adds the cross-function parameter bridge.

    Conservative — we only catch parameters explicitly typed as Path.
    Untyped parameters (`def scan_file(path):`) are not flagged because
    we can't infer intent, and they're a minority pattern in
    `scripts/`. Authors should annotate. The escape valve remains:
    explicit `try / except (FileNotFoundError, PermissionError)`.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.annotation is None:
                continue
            ann = arg.annotation
            # `path: Path` — annotation is Name("Path")
            if isinstance(ann, ast.Name) and ann.id == "Path":
                bound.add(arg.arg)
            # `path: pathlib.Path` — annotation is Attribute(pathlib).attr=Path
            elif (
                isinstance(ann, ast.Attribute)
                and ann.attr == "Path"
                and isinstance(ann.value, ast.Name)
                and ann.value.id == "pathlib"
            ):
                bound.add(arg.arg)
            # `path: Path | None` / `path: Optional[Path]` — recurse
            # into BinOp/Subscript to catch the union-with-None pattern
            else:
                for sub in ast.walk(ann):
                    if isinstance(sub, ast.Name) and sub.id == "Path":
                        bound.add(arg.arg)
                        break
                    if (
                        isinstance(sub, ast.Attribute)
                        and sub.attr == "Path"
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "pathlib"
                    ):
                        bound.add(arg.arg)
                        break
    return bound


def _scan_module(text: str, filename: str) -> list[str]:
    """Return list of human-readable findings for vulnerable call sites
    in this module. Empty list = file is clean.

    Two complementary sources of "race-prone path receivers":
      (1) Names directly bound by `for X in <obj>.glob/rglob(...)`.
      (2) Function parameters annotated as `Path` — captures the
          inter-procedural pattern `for py in rglob(): scan_file(py)`
          where the parameter `path: Path` inside scan_file IS a
          glob-yielded path even though the lexical binding is via
          parameter, not for-loop. Born v3 sharpening 2026-05-14
          after `audit_stale_doctrine_defaults.scan_file` slipped
          past v2.
    """
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    parents = _build_parent_map(tree)
    glob_bound = _names_bound_by_glob_loops(tree)
    path_param_bound = _path_typed_function_params(tree)
    race_prone_names = glob_bound | path_param_bound
    if not race_prone_names:
        return []  # nothing iterated via glob and no Path-typed params

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in _READ_METHODS:
            continue
        # Only consider calls where receiver is a Name bound by a glob
        # loop OR a Path-typed function parameter. Receiver could be
        # deeper (e.g.
        # `f.read_text().splitlines()`) but the read_text call is the
        # one that races; we look at THIS call's receiver.
        if not isinstance(func.value, ast.Name):
            continue
        if func.value.id not in race_prone_names:
            continue
        # Site found. Is it covered?
        if _is_in_race_handler(node, parents):
            continue  # explicit try/except (FileNotFoundError, PermissionError)
        findings.append(
            f"line {node.lineno}: `{func.value.id}.{func.attr}(...)` "
            f"inside glob/rglob loop without TOCTOU defense "
            f"(use safe_read_text({func.value.id}) OR wrap in "
            f"try/except (FileNotFoundError, PermissionError))"
        )
    return findings


def _candidate_scripts() -> list[Path]:
    """Return every Python file under scripts/ that is NOT exempt.
    Scope is intentionally broader than `audit_*.py` because the same
    bug class hit two helper scripts in 2026-05-14 (session_telemetry_
    harvester, suggest_test_exempts) — the preventer must catch them."""
    out: list[Path] = []
    for p in sorted(SCRIPTS_DIR.glob("*.py")):
        if p.name in _EXEMPT:
            continue
        out.append(p)
    return out


def main() -> int:
    findings: dict[str, list[str]] = {}
    scanned = 0
    for path in _candidate_scripts():
        text = safe_read_text(path)
        if text is None:
            # File raced away — same defense we're enforcing. Skip;
            # next preflight cycle will re-scan.
            continue
        scanned += 1
        site_findings = _scan_module(text, str(path))
        if site_findings:
            findings[path.name] = site_findings

    if findings:
        total = sum(len(v) for v in findings.values())
        print(
            f"audit_audit_io_safety: {total} vulnerable call site(s) "
            f"across {len(findings)} file(s) (out of {scanned} scanned):"
        )
        for fname, sites in findings.items():
            print(f"  {fname}:")
            for line in sites:
                print(f"    {line}")
        print()
        print(
            "Fix per site: replace `<X>.read_text(...)` with "
            "`safe_read_text(<X>)` from `_audit_io`, OR wrap the call in "
            "`try / except (FileNotFoundError, PermissionError)`."
        )
        return 1

    print(
        f"audit_audit_io_safety: clean — {scanned} script(s) scanned, "
        f"every glob+read site covered by safe_read_text or explicit "
        f"TOCTOU guard"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
