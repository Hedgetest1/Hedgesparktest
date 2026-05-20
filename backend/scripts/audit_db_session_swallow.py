#!/usr/bin/env python3
# invariant-eligible: false — static AST source-scan, no runtime state;
# sibling of audit_db_session_rollback (regression lock) and
# audit_savepoint_scope_no_inner_commit (semantic AST). Commit-stage gate only.
# (No runtime mutable state to check; meaningless to re-run every 15-min
# runtime cycle against unchanged source.)
"""audit_db_session_swallow.py — Stage 1 forward-preventer for the
write_no_rollback DB-session-poison class.

THE BUG CLASS:
  A shared-session DB write (db.add / db.flush / db.commit /
  db.execute(<INSERT|UPDATE|DELETE>)) inside a try whose except does
  NOT call db.rollback() and is NOT wrapped in
  `with savepoint_scope(db):` / `with db.begin_nested():`. The next
  caller using `db` (the shared session) inherits a poisoned txn →
  every subsequent op fails with InFailedSqlTransaction /
  PendingRollbackError, and the real work (or the alert that would
  have flagged it) is silently lost.

WHY THIS AUDIT:
  Three-layer DB-session protection. Existing layers:
    L2 RUNTIME: savepoint_scope (app/core/database.py, commit 42fc791)
       self-recovers BOTH commit-dissolution AND swallow-abort for any
       caller that uses it.
    L3 REGRESSION LOCK: audit_db_session_rollback.py — pins 16 already-
       fixed sites so a refactor cannot silently strip the guard.

  This audit completes **L1 STATIC FORWARD**: any NEW code that
  introduces a shared-session swallow-no-rollback site is caught at
  commit time, BEFORE it lands in production.

SCOPE (Stage 1 of 2 — born 2026-05-20):
  This commit lands the audit INFRASTRUCTURE: AST heuristic + non-
  vacuity self-test + opt-out marker (`# session-rollback: ok —
  <reason>`) + preflight info-only wiring + contract tests. The
  initial run produces a list of current candidates (the existing
  backlog scoped by SESSION_STATE / Agent a7de1ee12382855f5 at ~25
  sites, refined by Agent a51e6fe756df60fe0 to 29 in-scope).

  Stage 2 (next focused turn, R-blocker:sprint>1d): per-site
  verification of each current candidate (chatbot_llm_fallback:453
  spot-checked = request-scoped FastAPI dep teardown = framework-
  safe = annotate; cannot trust 🔴 classifications without per-site
  read of caller context). After all current candidates are fixed
  OR annotated, flip preflight wiring to STRICT.

EXCLUSIONS (filtered out by the AST heuristic — these are NOT bugs):
  1. Own-session pattern: `db = SessionLocal()` + `db.close()` in
     try/finally. The session is owned by this function; per-call
     failure mode; no caller poisoning.
  2. Already inside `with savepoint_scope(db):` /
     `with db.begin_nested():`. The wrapper handles rollback.
  3. Handler calls db.rollback() OR rollback_quiet(db).
  4. Read-only try body (only SELECT/WITH/EXPLAIN executes).
  5. Tests/migrations directories.
  6. Opt-out: `# session-rollback: ok — <reason>` on the try line
     or in the try/except body.

NON-VACUITY:
  --self-test runs 3 buggy + 3 safe inline snippets through the
  matcher; refuses to scan production if any classification flips.
  Defends against silent audit regression (smoke-fiction class,
  CLAUDE.md §19 359308e lesson).

CLI:
  audit_db_session_swallow.py [--report|--strict] [--self-test]
                              [--show-source]
  Default mode: --report (exit 0 with findings printed; preflight
  shows info, does NOT block). Flip to --strict in Stage 2.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
import tokenize
from io import StringIO

sys.path.insert(0, "/opt/wishspark/backend")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _audit_io import safe_read_text  # noqa: E402
from _audit_telemetry_shim import telemetered  # noqa: E402

_APP = pathlib.Path("/opt/wishspark/backend/app")
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", "tests", "migrations"}

# Session-like parameter names (any of these as a function argument
# means the function operates on a CALLER's shared session).
_SESSION_NAMES = {"db", "session", "sess", "_db", "_session"}

# Factories that create an OWN session (locally owned, not shared).
_OWN_SESSION_FACTORIES = {"SessionLocal", "Session", "ReadSession", "sessionmaker"}

# Attribute calls that constitute a WRITE on a session.
_WRITE_ATTRS = {
    "add", "add_all", "flush", "commit", "delete", "merge",
    "bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings",
}

# Rollback-equivalent helpers.
_ROLLBACK_HELPERS = {"rollback_quiet"}

# SAVEPOINT wrappers — if a try is inside any of these contexts, skip.
_SAVEPOINT_CTX_NAMES = {"savepoint_scope", "begin_nested"}

# Opt-out comment pattern.
_OPTOUT_RE = re.compile(r"#\s*session-rollback:\s*ok", re.IGNORECASE)

# SQL-write prefixes (after upper + strip).
_WRITE_SQL_PREFIXES = (
    "INSERT ", "UPDATE ", "DELETE ", "MERGE ", "TRUNCATE ",
    "ALTER ", "CREATE ", "DROP ",
)
_READ_SQL_PREFIXES = ("SELECT ", "WITH ", "EXPLAIN ")


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _iter_py(root: pathlib.Path):
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _collect_optout_lines(src: str) -> set[int]:
    """Use tokenize so we don't false-match # inside string literals."""
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(StringIO(src).readline):
            if tok.type == tokenize.COMMENT and _OPTOUT_RE.search(tok.string):
                out.add(tok.start[0])
    except (tokenize.TokenizeError, IndentationError):
        pass
    return out


def _receiver_name(call: ast.Call) -> str | None:
    """If call is X.method(), return X's bare name."""
    f = call.func
    if not isinstance(f, ast.Attribute):
        return None
    recv = f.value
    if isinstance(recv, ast.Name):
        return recv.id
    if isinstance(recv, ast.Attribute):
        return recv.attr
    return None


def _is_sessionlocal_call(node: ast.AST) -> bool:
    """True iff node is a Call to one of the OWN_SESSION_FACTORIES."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name) and f.id in _OWN_SESSION_FACTORIES:
        return True
    if isinstance(f, ast.Attribute) and f.attr in _OWN_SESSION_FACTORIES:
        return True
    return False


def _own_session_names(fn: ast.AST) -> set[str]:
    """Names locally assigned from a SessionLocal()-like factory in fn."""
    owned: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and _is_sessionlocal_call(n.value):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    owned.add(tgt.id)
        elif isinstance(n, ast.With):
            for item in n.items:
                if _is_sessionlocal_call(item.context_expr):
                    if isinstance(item.optional_vars, ast.Name):
                        owned.add(item.optional_vars.id)
    return owned


def _is_savepoint_ctx(with_node: ast.With) -> bool:
    """True iff a With statement uses savepoint_scope / begin_nested."""
    for item in with_node.items:
        expr = item.context_expr
        if isinstance(expr, ast.Call):
            f = expr.func
            if isinstance(f, ast.Name) and f.id in _SAVEPOINT_CTX_NAMES:
                return True
            if isinstance(f, ast.Attribute) and f.attr in _SAVEPOINT_CTX_NAMES:
                return True
    return False


def _extract_sql_from_node(node: ast.AST) -> str | None:
    """Extract upper-stripped SQL prefix from a node (Constant string,
    JoinedStr, or text(...) Call wrapping either). Returns None if
    the node doesn't resolve to a literal SQL fragment."""
    # text(...) Call wrapping literal
    if isinstance(node, ast.Call):
        f = node.func
        is_text_call = (
            (isinstance(f, ast.Name) and f.id == "text")
            or (isinstance(f, ast.Attribute) and f.attr == "text")
        )
        if is_text_call and node.args:
            return _extract_sql_from_node(node.args[0])
    # Direct string literal
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip().upper()
    # f-string: peek leading Constant
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value.strip().upper()
    return None


def _resolve_variable_in_scope(name: str, func: ast.AST, cutoff_line: int) -> str | None:
    """For a variable `name` referenced at `cutoff_line`, scan the
    enclosing function body for the LATEST assignment `name = <value>`
    BEFORE cutoff_line and try to extract the SQL prefix from <value>.

    Returns the upper-stripped SQL string if resolvable, None
    otherwise. Closes the "dynamic SQL → assume-read" gap (§21
    11/10 pushback) where a developer writes::

        query = "UPDATE merchants SET x=1"
        db.execute(text(query))

    Without resolution, the audit silently treated this as read-only.
    """
    latest_sql: str | None = None
    latest_line = -1
    for n in ast.walk(func):
        if not isinstance(n, ast.Assign):
            continue
        if n.lineno >= cutoff_line:
            continue
        for tgt in n.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name:
                sql = _extract_sql_from_node(n.value)
                if sql is not None and n.lineno > latest_line:
                    latest_sql = sql
                    latest_line = n.lineno
    return latest_sql


def _extract_sql(call: ast.Call, func: ast.AST | None = None) -> str | None:
    """If call is db.execute(text("...")) or db.execute("...") or
    db.execute(text(f"...")) or db.execute(text(<variable>)), return
    the UPPER-stripped SQL prefix. Returns None when the SQL is too
    dynamic to peek at (parameter / .format() / cross-function flow).

    The `func` parameter enables scope-walk variable resolution:
    when the SQL argument is a bare Name, walk the enclosing function
    for the latest assignment and extract from there. Born 2026-05-20
    closing the "dynamic SQL → assume-read" gap (§21 11/10 pushback).
    """
    if not call.args:
        return None
    arg = call.args[0]
    # text("...") / text(f"...") / direct literal / f-string
    sql = _extract_sql_from_node(arg)
    if sql is not None:
        return sql
    # text(<Name>) — scope-walk resolution
    if (
        isinstance(arg, ast.Call)
        and func is not None
    ):
        f = arg.func
        is_text_call = (
            (isinstance(f, ast.Name) and f.id == "text")
            or (isinstance(f, ast.Attribute) and f.attr == "text")
        )
        if is_text_call and arg.args and isinstance(arg.args[0], ast.Name):
            return _resolve_variable_in_scope(
                arg.args[0].id, func, arg.lineno
            )
    # Direct Name as first arg: db.execute(<Name>)
    if isinstance(arg, ast.Name) and func is not None:
        return _resolve_variable_in_scope(arg.id, func, call.lineno)
    return None


def _is_write_call(
    call: ast.Call,
    session_names: set[str],
    func: ast.AST | None = None,
) -> tuple[bool, str]:
    """True + description iff this call is a write on a session-named receiver.

    Returns (is_write, label) where label is e.g. "db.add" / "db.execute(UPDATE)".

    The `func` parameter enables scope-walk SQL resolution for
    `db.execute(text(<variable>))` patterns: when the SQL is in a
    local variable, the audit walks back to its assignment instead of
    blindly assuming read.
    """
    f = call.func
    if not isinstance(f, ast.Attribute):
        return False, ""
    recv = f.value
    if not isinstance(recv, ast.Name) or recv.id not in session_names:
        return False, ""
    attr = f.attr
    if attr in _WRITE_ATTRS:
        return True, f"{recv.id}.{attr}"
    if attr == "execute":
        sql = _extract_sql(call, func)
        if sql is None:
            # Dynamic / variable-referenced SQL: signal-noise tradeoff.
            # Empirically, ~90% of dynamic-SQL execute calls are SELECT
            # queries built from a variable. Flagging them all floods the
            # audit (147→noise). Default = assume-read; the real write
            # cases are covered by explicit `db.add` / `db.flush` /
            # `db.commit` / `db.delete` / `db.merge` and by literal-SQL
            # detection above. The tradeoff is documented; the audit's
            # job is high-signal forward prevention, not exhaustive.
            return False, ""
        for p in _WRITE_SQL_PREFIXES:
            if sql.startswith(p):
                return True, f"{recv.id}.execute({p.strip()})"
        # Anything not starting with a write prefix → not a write
        # (SELECT/WITH/EXPLAIN/COPY-from etc.). Defaults to read.
        return False, ""
    return False, ""


def _handler_rolls_back(handler: ast.ExceptHandler, session_names: set[str]) -> bool:
    """True iff the except handler body calls a rollback-equivalent."""
    for n in ast.walk(handler):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        # session.rollback()
        if isinstance(f, ast.Attribute) and f.attr == "rollback":
            recv = f.value
            if isinstance(recv, ast.Name) and recv.id in session_names:
                return True
        # rollback_quiet(db)
        if isinstance(f, ast.Name) and f.id in _ROLLBACK_HELPERS:
            if n.args:
                arg0 = n.args[0]
                if isinstance(arg0, ast.Name) and arg0.id in session_names:
                    return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Visitor with parent stack
# ──────────────────────────────────────────────────────────────────────
class _SwallowVisitor(ast.NodeVisitor):
    def __init__(self, path: pathlib.Path, optout_lines: set[int]):
        self.path = path
        self.optout_lines = optout_lines
        self.parent_stack: list[ast.AST] = []
        self.findings: list[dict] = []

    def generic_visit(self, node):  # type: ignore[override]
        self.parent_stack.append(node)
        try:
            super().generic_visit(node)
        finally:
            self.parent_stack.pop()

    def _enclosing_func(self) -> ast.AST | None:
        for n in reversed(self.parent_stack):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return n
        return None

    def _func_param_names(self) -> set[str]:
        fn = self._enclosing_func()
        if not fn:
            return set()
        return {a.arg for a in fn.args.args}  # type: ignore[attr-defined]

    def _enclosed_in_savepoint(self) -> bool:
        for n in self.parent_stack:
            if isinstance(n, ast.With) and _is_savepoint_ctx(n):
                return True
        return False

    def visit_Try(self, node: ast.Try):  # type: ignore[override]
        # 1. Opt-out — scan from 5 lines BEFORE the try (catches comments
        # placed above the try statement) through the try body.
        for lineno in range(
            max(1, node.lineno - 5),
            (node.end_lineno or node.lineno) + 1,
        ):
            if lineno in self.optout_lines:
                self.generic_visit(node)
                return

        # 2a. SAVEPOINT wrapping the try (parent-stack)
        if self._enclosed_in_savepoint():
            self.generic_visit(node)
            return

        # 2b. SAVEPOINT INSIDE the try as the immediate protective scope.
        # Pattern: `try: with db.begin_nested(): <writes>` — the writes
        # are wrapped in a SAVEPOINT; the outer except catches the
        # release/rollback failure but the wrapper handled the txn.
        if (
            len(node.body) == 1
            and isinstance(node.body[0], ast.With)
            and _is_savepoint_ctx(node.body[0])
        ):
            self.generic_visit(node)
            return

        # 3. Determine session-like names in scope (param OR own-local)
        fn = self._enclosing_func()
        if fn is None:
            self.generic_visit(node)
            return
        params = self._func_param_names()
        session_params = params & _SESSION_NAMES
        own_locals = _own_session_names(fn)
        all_session_names = session_params | own_locals

        if not all_session_names:
            self.generic_visit(node)
            return

        # 4. Find writes in try body — only WRITES on SHARED (param) sessions
        # qualify. Writes on OWN locals are excluded (own-session pattern).
        writes: list[tuple[ast.Call, str]] = []
        for n in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(n, ast.Call):
                # First check: is the receiver a shared (param) session?
                # We pass ONLY session_params here (not own_locals) so own-
                # session calls are filtered out at the write-detection step.
                if not session_params:
                    continue
                # Pass the enclosing function for variable scope-walk
                # resolution (`db.execute(text(<variable>))` lookup).
                is_w, label = _is_write_call(n, session_params, func=fn)
                if is_w:
                    writes.append((n, label))
        if not writes:
            self.generic_visit(node)
            return

        # 5. Check handlers
        for handler in node.handlers:
            if _handler_rolls_back(handler, session_params):
                self.generic_visit(node)
                return

        # 6. EMIT — find the first non-rollback handler for the excerpt
        first_call, first_label = writes[0]
        handler_excerpt = (
            f"line {node.handlers[0].lineno}"
            if node.handlers else "no-handler"
        )
        self.findings.append({
            "path": str(self.path),
            "line": node.lineno,
            "session": next(iter(session_params)),
            "first_write": first_label,
            "first_write_line": first_call.lineno,
            "handler": handler_excerpt,
        })
        self.generic_visit(node)


def scan_file(path: pathlib.Path) -> list[dict]:
    src = safe_read_text(path)
    if src is None:
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    optout_lines = _collect_optout_lines(src)
    visitor = _SwallowVisitor(path, optout_lines)
    visitor.visit(tree)
    return visitor.findings


# ──────────────────────────────────────────────────────────────────────
# Non-vacuity self-test
# ──────────────────────────────────────────────────────────────────────
_TEST_BUGGY = [
    # B1: bare swallow on db.flush
    '''
def handler(db):
    try:
        db.add(Foo()); db.flush()
    except Exception:
        log.warning("oops")
''',
    # B2: bare except, db.execute(UPDATE)
    '''
def handler(db):
    try:
        db.execute(text("UPDATE merchants SET x=1"))
    except Exception:
        pass
''',
    # B3: db.commit raise, swallowed
    '''
def handler(db):
    try:
        db.commit()
    except SQLAlchemyError as e:
        log.error("nope")
''',
]

_TEST_SAFE = [
    # S1: handler rolls back
    '''
def handler(db):
    try:
        db.add(Foo()); db.flush()
    except Exception:
        db.rollback()
''',
    # S2: own session — local SessionLocal()
    '''
def own_handler():
    db = SessionLocal()
    try:
        db.add(Foo()); db.flush()
    except Exception:
        log.error("ok")
    finally:
        db.close()
''',
    # S3: outer savepoint_scope wraps
    '''
def wrapped(db):
    with savepoint_scope(db):
        try:
            db.add(Foo())
        except Exception:
            raise
''',
]


def _scan_snippet(src: str) -> list[dict]:
    tree = ast.parse(src)
    optout_lines = _collect_optout_lines(src)
    v = _SwallowVisitor(pathlib.Path("<inline>"), optout_lines)
    v.visit(tree)
    return v.findings


def run_self_test(verbose: bool = True) -> int:
    fails: list[str] = []
    for i, src in enumerate(_TEST_BUGGY, 1):
        n = len(_scan_snippet(src))
        if n < 1:
            fails.append(f"B{i}: expected ≥1 finding, got {n}")
    for i, src in enumerate(_TEST_SAFE, 1):
        n = len(_scan_snippet(src))
        if n != 0:
            fails.append(f"S{i}: expected 0 findings, got {n}")
    if fails:
        for f in fails:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    if verbose:
        print("SELF-TEST: 3 buggy flagged, 3 safe not flagged — heuristic intact")
    return 0


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
@telemetered("audit_db_session_swallow")
def main(argv) -> int:
    p = argparse.ArgumentParser(
        description="Stage 1 forward-preventer for write_no_rollback class."
    )
    p.add_argument(
        "--self-test", action="store_true",
        help="Run non-vacuity self-test and exit.",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if ANY candidates found (Stage 2 mode).",
    )
    p.add_argument(
        "--report", action="store_true",
        help="Report findings, exit 0 (Stage 1 mode, DEFAULT).",
    )
    p.add_argument(
        "--show-source", action="store_true",
        help="Print source excerpts for each finding.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress informational output on GREEN.",
    )
    args = p.parse_args(argv)

    if args.self_test:
        return run_self_test()

    # Production scan refuses if self-test regresses
    if run_self_test(verbose=False) != 0:
        print(
            "audit_db_session_swallow: self-test failed — "
            "refusing to scan production source",
            file=sys.stderr,
        )
        return 2

    findings: list[dict] = []
    for path in _iter_py(_APP):
        findings.extend(scan_file(path))

    # Default mode = report (Stage 1)
    strict = args.strict and not args.report

    if not findings:
        if not args.quiet:
            print(
                "audit_db_session_swallow: GREEN — 0 shared-session "
                "swallow-no-rollback candidates"
            )
        return 0

    mode = "STRICT" if strict else "REPORT"
    print(
        f"audit_db_session_swallow: {mode} — "
        f"{len(findings)} shared-session swallow-no-rollback candidate(s)"
    )
    for f in findings:
        rel = pathlib.Path(f["path"]).relative_to("/opt/wishspark/backend")
        print(
            f"  {rel}:{f['line']}: try wraps {f['first_write']} "
            f"(line {f['first_write_line']}) on session=`{f['session']}`, "
            f"handler {f['handler']} — no rollback"
        )
    print()
    print("Each finding is one of:")
    print("  (a) genuine bug — fix via db.rollback() in the handler OR")
    print("      rollback_quiet(db) helper OR `with savepoint_scope(db):`.")
    print("  (b) safe-by-context — add `# session-rollback: ok — <reason>`")
    print("      to the try line. Document WHY it's safe (request-scoped")
    print("      FastAPI teardown, caller wrapper, etc.).")

    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
