#!/usr/bin/env python3
# invariant-eligible: false — static AST audit over SOURCE; commit-stage
# only (a SOURCE edit that wraps a committing body in savepoint_scope).
"""audit_savepoint_scope_no_inner_commit.py — the STATIC counterpart to
savepoint_scope's runtime self-enforcing guard (born 2026-05-19c).

THE BUG CLASS (d15ada0 #1, shipped + silently regressed Klaviyo
Pro-push): `with savepoint_scope(db):` wrapping a body that — directly
OR transitively through an app/-local helper — issues a full
`db.commit()` / `session.commit()`. The inner full commit dissolves
the SAVEPOINT; savepoint_scope's nested.commit() then raises
ResourceClosedError on the FIRST execution. The runtime guard
(database.py: `if not nested.is_active: raise`) catches it at run
time; THIS audit catches it at PREFLIGHT — defense in depth, so a
misclassified site (savepoint_scope where rollback_quiet was correct)
can never be committed again.

Approach (depth-bounded, app/-local name resolution — paired with the
runtime guard as the documented bound; a perfect cross-module import
graph is out of scope and unnecessary given the runtime backstop):
  1. Index every app/ FunctionDef by name → [(file, node)].
  2. Find every `with savepoint_scope(...)` block.
  3. Walk its body for Calls. Direct hit: `.commit`/`.rollback` on a
     session-ish receiver. Transitive: resolve the callee name in the
     index, recurse depth ≤ 5 with a visited set.
  4. FP exclusions: the savepoint_scope definition file; receivers
     named nested/savepoint/sp (the savepoint object); redis-ish
     receivers; a helper that commits its OWN SessionLocal()/
     ReadSession(); the opt-out `# savepoint-scope: commits-own-session`
     comment on the `with` line.

Exit 0 = clean; 1 = a savepoint_scope wraps a (transitively)
committing body OR the audit went vacuous (no sites discovered).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from _audit_io import safe_read_text  # race-safe rglob reads (TOCTOU)

_APP = Path(__file__).resolve().parent.parent / "app"
_DATABASE_PY = (_APP / "core" / "database.py").resolve()  # the def lives here
_MIN_SITES = 6                # live count of real `with savepoint_scope(` usages
_MAX_DEPTH = 5
_SESSION_RECEIVERS = {"db", "session", "s", "sess", "_db"}
_SAVEPOINT_RECEIVERS = {"nested", "savepoint", "sp", "_sp"}
_OWN_SESSION_FACTORIES = {"SessionLocal", "ReadSession", "sessionmaker"}
_OPT_OUT = "savepoint-scope: commits-own-session"


def _iter_py():
    for p in _APP.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _commit_receiver_name(call: ast.Call) -> str | None:
    """If `call` is X.commit()/X.rollback(), return X's bare name."""
    f = call.func
    if not (isinstance(f, ast.Attribute) and f.attr in ("commit", "rollback")):
        return None
    recv = f.value
    if isinstance(recv, ast.Name):
        return recv.id
    if isinstance(recv, ast.Attribute):
        return recv.attr
    return None


def _own_session_locals(fn: ast.AST) -> set[str]:
    """Names assigned from SessionLocal()/ReadSession() within fn — a
    commit on these is the helper's OWN session, not the caller's."""
    owned: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            cf = n.value.func
            fname = (cf.id if isinstance(cf, ast.Name)
                     else cf.attr if isinstance(cf, ast.Attribute) else "")
            if fname in _OWN_SESSION_FACTORIES:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        owned.add(t.id)
    return owned


def _direct_commit(node: ast.AST, fn_scope: ast.AST) -> bool:
    owned = _own_session_locals(fn_scope)
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        recv = _commit_receiver_name(c)
        if recv is None:
            continue
        if recv in _SAVEPOINT_RECEIVERS or recv in owned:
            continue  # savepoint object OR helper's own session
        # session-ish receiver OR generic `db.`-style → flag
        if recv in _SESSION_RECEIVERS or recv.endswith("db") or recv == "session":
            return True
    return False


def main() -> int:
    # 1. index app/ defs by name
    defs: dict[str, list[tuple[Path, ast.AST]]] = {}
    trees: dict[Path, ast.AST] = {}
    for p in _iter_py():
        _src = safe_read_text(p)
        if _src is None:
            continue
        try:
            t = ast.parse(_src)
        except SyntaxError:
            continue
        trees[p] = t
        for n in ast.walk(t):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(n.name, []).append((p, n))

    def body_commits(node: ast.AST, scope: ast.AST, depth: int,
                      visited: set) -> bool:
        if depth > _MAX_DEPTH:
            return False
        if _direct_commit(node, scope):
            return True
        for c in ast.walk(node):
            if not isinstance(c, ast.Call):
                continue
            fn = c.func
            # Resolve ONLY bare-name function calls `foo(...)` (imported
            # or local functions — e.g. update_product_opportunity,
            # write_audit_log). Do NOT resolve `obj.method(...)` by its
            # bare `.attr`: a method name like `.add`/`.get`/`.first`/
            # `.query` collides with unrelated top-level defs of the
            # same name and was the false-positive source (nudge_
            # compose_task:70 flagged via `compose_nudge_variants`'s
            # `.add`/`.get` chain). Direct session `.commit()`/
            # `.rollback()` on a method receiver is already caught by
            # `_direct_commit`; this branch is purely the transitive
            # FUNCTION-call resolver. The runtime self-enforcing guard
            # in savepoint_scope is the documented backstop for the
            # residual (a committing helper reached only via a method
            # call) — defense in depth, per the audit docstring.
            if not isinstance(fn, ast.Name):
                continue
            name = fn.id
            if name in ("commit", "rollback", "savepoint_scope"):
                continue
            for (fp, fnode) in defs.get(name, []):
                if fp.resolve() == _DATABASE_PY:
                    continue  # never recurse into the primitive's own file
                key = (str(fp), name)
                if key in visited:
                    continue
                visited.add(key)
                if body_commits(fnode, fnode, depth + 1, visited):
                    return True
        return False

    sites = 0
    transitive_exercised = 0
    violations: list[str] = []

    for p, t in trees.items():
        if p.resolve() == _DATABASE_PY:
            continue  # the savepoint_scope definition / docstring idiom
        _src2 = safe_read_text(p)
        if _src2 is None:
            continue  # vanished between the index pass and here (TOCTOU)
        src_lines = _src2.splitlines()
        for n in ast.walk(t):
            if not isinstance(n, (ast.With, ast.AsyncWith)):
                continue
            is_sp = any(
                isinstance(it.context_expr, ast.Call) and (
                    (isinstance(it.context_expr.func, ast.Name)
                     and it.context_expr.func.id == "savepoint_scope")
                    or (isinstance(it.context_expr.func, ast.Attribute)
                        and it.context_expr.func.attr == "savepoint_scope")
                )
                for it in n.items
            )
            if not is_sp:
                continue
            ln = getattr(n, "lineno", 0)
            if 0 < ln <= len(src_lines) and _OPT_OUT in src_lines[ln - 1]:
                continue  # explicit opt-out
            sites += 1
            # enclosing function = scope for own-session detection
            scope = n
            for fn in ast.walk(t):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if any(x is n for x in ast.walk(fn)):
                        scope = fn
                        break
            visited: set = set()
            before = len(visited)
            hit = False
            for stmt in n.body:
                if body_commits(stmt, scope, 0, visited):
                    hit = True
                    break
            if len(visited) > before:
                transitive_exercised += 1
            if hit:
                violations.append(
                    f"{p.relative_to(_APP.parent)}:{ln}  `with "
                    f"savepoint_scope` wraps a body that (transitively) "
                    f"issues a full session commit/rollback → the "
                    f"SAVEPOINT is dissolved. This site MUST use "
                    f"rollback_quiet(db) (the helper is already "
                    f"per-iteration durable). See "
                    f"project_db_session_rollback_class_sweep_2026_05_19."
                )

    if violations:
        print("FAIL: savepoint_scope wrapping a committing body "
              "(d15ada0 #1 class):")
        for v in violations:
            print(f"  - {v}")
        return 1

    if sites < _MIN_SITES:
        print(f"FAIL (vacuous): only {sites} `with savepoint_scope` "
              f"site(s) found, expected ≥{_MIN_SITES} — parser/name "
              f"drift, the audit is not actually checking anything.")
        return 1

    # Non-vacuity = "found ≥ floor real savepoint sites" (proves the
    # scanner runs against real code). Whether any of those bodies
    # happens to call a bare-name resolvable function is a CODEBASE
    # property, not an audit-health invariant — baking
    # `transitive_exercised >= 1` in here wrongly fails minimal trees
    # / codebases whose savepoint bodies are method-call-only. The
    # transitive resolver's correctness is proven by the contract test
    # (test_detects_transitive_committing_helper), where it belongs.
    print(f"OK: {sites} `with savepoint_scope` site(s), none wrap a "
          f"(transitively) committing body; {transitive_exercised} "
          f"exercised the depth-bounded transitive resolver.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
