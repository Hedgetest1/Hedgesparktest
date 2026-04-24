#!/usr/bin/env python
"""
audit_exception_sinks.py — Find dangerous exception swallows.

An exception swallow is legitimate when:
  * It wraps a non-critical best-effort path (telemetry, logging)
  * The caller has already committed/rolled back its transaction

It is DANGEROUS when:
  * It hides a write path that may have partially committed
  * It returns success on failure
  * It happens inside a DB transaction without rollback
  * It uses `except: pass` bare (no logging, no alert)
  * It happens in the request path and would mask a 500

We flag the dangerous patterns using AST analysis so we see the
structure, not just the text.

Heuristics:
  1. `except Exception: pass` with no logging in the function — bare
     swallow, no audit trail.
  2. `try: db.execute(...); db.commit()` followed by `except: pass`
     with no `db.rollback()` — leaves the ORM session in an
     inconsistent state.
  3. `except: return True` / `except: return "ok"` — lying to caller.
  4. `except BaseException` — catches KeyboardInterrupt + SystemExit,
     which is almost always wrong.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from collections import defaultdict

APP_ROOT = pathlib.Path("/opt/wishspark/backend/app")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


class Finding:
    __slots__ = ("file", "line", "kind", "detail")

    def __init__(self, file: str, line: int, kind: str, detail: str):
        self.file = file
        self.line = line
        self.kind = kind
        self.detail = detail


def is_bare_pass(handler: ast.ExceptHandler) -> bool:
    """Body is literally just `pass`."""
    return (
        len(handler.body) == 1
        and isinstance(handler.body[0], ast.Pass)
    )


def handler_logs_or_alerts(handler: ast.ExceptHandler) -> bool:
    """Does this handler contain any logging / alerting call?"""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr.lower()
                if name in {
                    "debug", "info", "warning", "error", "critical",
                    "exception", "write_alert", "log", "sentry_capture",
                    "capture_exception", "capture_message",
                }:
                    return True
            elif isinstance(func, ast.Name) and func.id.lower() == "log":
                return True
    return False


def handler_returns_truthy(handler: ast.ExceptHandler) -> tuple[bool, str]:
    """Returns (True, literal) if the handler returns a non-None/False value."""
    for node in handler.body:
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Constant):
                if v.value in (True, "ok", "success", 1) or (
                    isinstance(v.value, str) and v.value.lower() in {"ok", "success", "done"}
                ):
                    return True, repr(v.value)
            elif isinstance(v, ast.Tuple):
                # (True, ...) / (ok, ...)
                if v.elts and isinstance(v.elts[0], ast.Constant) and v.elts[0].value is True:
                    return True, "(True, ...)"
    return False, ""


def _function_text_for_handler(src: str, handler: ast.ExceptHandler) -> str:
    """Return roughly the surrounding function source so we can scan
    for fail-open / fail-closed markers in nearby comments and
    docstrings. Window: 30 lines before, 12 after — generous before
    so doc comments at the top of a logical block (like a function
    docstring or a try-block lead-in) are still caught."""
    lines = src.splitlines()
    start = max(0, handler.lineno - 31)
    end = min(len(lines), handler.lineno + 12)
    return "\n".join(lines[start:end]).lower()


def is_intentional_fail_open(src: str, handler: ast.ExceptHandler) -> bool:
    """True if the handler is explicitly documented as a deliberate
    fail-open / fail-closed / best-effort path. Reduces lying_return
    false positives on intentionally-graceful degradation."""
    window = _function_text_for_handler(src, handler)
    return any(marker in window for marker in _FAIL_OPEN_MARKERS)


# Receiver names we trust as "this is a SQLAlchemy session/connection".
# Removed `"s"` (too generic — matched dict.set / set ops / Series ops).
_DB_SESSION_NAMES = {"db", "session", "self_db", "conn", "connection"}

# Markers we recognize as deliberate fail-open/fail-closed signals so
# we don't flag intentional graceful-degradation patterns as
# lying_return. Looked for in the function source AND in comments
# inside the handler body.
_FAIL_OPEN_MARKERS = (
    "fail-open", "fail open", "fail_open",
    "fail-closed", "fail closed", "fail_closed",
    "best-effort", "best effort", "non-fatal",
    "graceful", "swallow", "ignore",
)


def _call_target_is_db(func: ast.Attribute) -> bool:
    """
    Return True if the call target is likely a SQLAlchemy Session / connection.

    Heuristic: the object receiver must be a Name in _DB_SESSION_NAMES, or
    an attribute chain ending in one of those (e.g. self.db.commit()).
    Rejects rc.* / redis.* / r.*  (Redis writes don't need rollback).
    """
    receiver = func.value
    while isinstance(receiver, ast.Attribute):
        receiver = receiver.value
    if isinstance(receiver, ast.Name):
        return receiver.id in _DB_SESSION_NAMES
    if isinstance(receiver, ast.Call):
        # E.g. SessionLocal().commit() — also a DB call
        if isinstance(receiver.func, ast.Name) and receiver.func.id in {
            "SessionLocal", "Session",
        }:
            return True
    return False


def try_has_write_without_rollback(try_node: ast.Try) -> bool:
    """
    A try block contains real write calls but no handler performs a
    rollback or re-raises.

    Writes are:
      * db.commit()                   (unconditional write)
      * db.add(...)                   (ORM add)
      * db.flush()                    (flushes pending writes)
      * db.delete(obj)                (ORM delete)
      * db.merge(...)                 (ORM merge — upsert-like, 2026-04-23 retro DA)
      * db.bulk_insert_mappings(...)  (ORM bulk insert, 2026-04-23 retro DA)
      * db.bulk_update_mappings(...)  (ORM bulk update, 2026-04-23 retro DA)
      * db.bulk_save_objects(...)     (ORM bulk save, 2026-04-23 retro DA)
      * db.execute(text("INSERT... | UPDATE... | DELETE... | MERGE..."))

    Writes are NOT:
      * db.execute(text("SELECT ..."))  (read, nothing to roll back)
      * rc.set / rc.setex / rc.delete   (Redis — no transaction)

    Caller-owned transactions are EXEMPT: if the try block has only
    add/flush/delete (no commit), the function is appending to a
    session that the caller will commit/rollback. Flagging these
    creates noise. The audit only flags blocks where the function
    explicitly owns the transaction (calls commit) — those MUST
    rollback on failure or leave the session in a bad state.
    """
    has_write = False
    has_own_commit = False
    for node in ast.walk(ast.Module(body=try_node.body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not _call_target_is_db(func):
            continue
        name = func.attr.lower()
        if name == "commit":
            has_write = True
            has_own_commit = True
            continue
        if name in {
            "flush", "add", "delete", "merge",
            "bulk_insert_mappings", "bulk_update_mappings", "bulk_save_objects",
            "add_all",
        }:
            has_write = True
            continue
        if name == "execute":
            # Check the first positional arg for an INSERT/UPDATE/DELETE literal
            if node.args:
                arg0 = node.args[0]
                sql_text: str | None = None
                # Common shapes:
                #   execute(text("..."))          -> arg0 = Call(Name('text'), [Constant(...)])
                #   execute("...")                -> arg0 = Constant
                #   execute(text("""..."""))      -> same as above
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    sql_text = arg0.value
                elif isinstance(arg0, ast.Call) and arg0.args:
                    inner = arg0.args[0]
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        sql_text = inner.value
                elif isinstance(arg0, ast.JoinedStr):
                    # f-string — just check the literal parts
                    parts = [
                        v.value for v in arg0.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    ]
                    sql_text = " ".join(parts)
                if sql_text:
                    head = sql_text.strip().upper()[:15]
                    if head.startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE")):
                        has_write = True
                        continue
    if not has_write:
        return False
    # Caller-owned transaction (no own commit) — exempt. The caller is
    # expected to handle rollback at its own try/except boundary.
    if not has_own_commit:
        return False

    any_handler_rolls_back = False
    any_handler_reraises = False
    for h in try_node.handlers:
        # Check for rollback call
        for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "rollback":
                    any_handler_rolls_back = True
                    break
        # Check for raise / re-raise
        for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
            if isinstance(node, ast.Raise):
                any_handler_reraises = True
                break

    # `try: write; commit; finally: db.close()` is the standard SQLAlchemy
    # pattern: db.close() implicitly rolls back any pending transaction.
    # We accept a close() call ANYWHERE inside the try body or its
    # finalbody — this catches nested patterns like:
    #     try:                          ← this is what the audit flagged
    #         db = SessionLocal()
    #         try:
    #             write_alert(...)
    #             db.commit()
    #         finally:
    #             db.close()            ← close lives inside the inner try
    #     except Exception:
    #         pass
    has_close = False
    scan_nodes = list(try_node.body) + list(try_node.finalbody or [])
    for stmt in scan_nodes:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "close":
                    has_close = True
                    break
        if has_close:
            break

    return not (any_handler_rolls_back or any_handler_reraises or has_close)


def handler_catches_base(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True  # bare except
    if isinstance(t, ast.Name) and t.id == "BaseException":
        return True
    if isinstance(t, ast.Tuple):
        return any(
            isinstance(el, ast.Name) and el.id == "BaseException"
            for el in t.elts
        )
    return False


def audit_file(path: pathlib.Path) -> list[Finding]:
    try:
        src = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    findings: list[Finding] = []
    rel = str(path.relative_to(APP_ROOT.parent))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for h in node.handlers:
            line = h.lineno
            # 1. bare pass + no logging
            if is_bare_pass(h) and not handler_logs_or_alerts(h):
                findings.append(Finding(rel, line, "bare_pass", "except ...: pass (no logging)"))
            # 2. lying return — but only if NOT documented as deliberate.
            #    A handler is "documented" when:
            #      (a) the surrounding source has a fail-open / fail-closed
            #          / best-effort marker, OR
            #      (b) the handler emits a log/warning/alert call (operator
            #          gets visibility on the fallback even without the marker)
            truthy, lit = handler_returns_truthy(h)
            if truthy and not is_intentional_fail_open(src, h) and not handler_logs_or_alerts(h):
                findings.append(Finding(rel, line, "lying_return", f"except ...: return {lit}"))
            # 3. BaseException catch
            if handler_catches_base(h) and h.type is not None:
                findings.append(Finding(rel, line, "catches_base", "except BaseException"))
        # 4. write without rollback across all handlers — but only if
        #    NOT documented as a deliberate non-fatal best-effort path
        #    (same fail-open marker check we use for lying_return)
        if try_has_write_without_rollback(node):
            # Check the source window around any handler in this try
            # for the marker. If at least one handler is documented,
            # accept the whole try as deliberately graceful.
            documented = any(is_intentional_fail_open(src, h) for h in node.handlers)
            if not documented:
                findings.append(Finding(
                    rel, node.lineno, "write_no_rollback",
                    "try block has write ops but no handler rolls back or re-raises",
                ))

    return findings


CRITICAL_KINDS = ("write_no_rollback", "lying_return")


def main(argv: list[str] | None = None) -> int:
    """Exit codes:
      * 0 — no findings at the requested severity
      * 1 — findings at the requested severity exist (blocking)

    Flags:
      --critical-only   Block only on CRITICAL kinds (write_no_rollback,
                        lying_return). bare_pass + catches_base are
                        printed as INFO but do NOT fail the audit.
                        Used by preflight (Tier 2.2.b) so the 4 SINK
                        class shipped 2026-04-23/24 stays at zero.
      (no flag)         Legacy behavior — any finding fails.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    # --strict is an alias for --critical-only in this audit (bare_pass
    # and catches_base are stylistic INFO, never block). This lets
    # invariant_monitor's generic `--strict` runner reuse the audit
    # without per-audit flag plumbing.
    critical_only = "--critical-only" in argv or "--strict" in argv

    all_findings: dict[str, list[Finding]] = defaultdict(list)
    for py in APP_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        for f in audit_file(py):
            all_findings[f.kind].append(f)

    total = sum(len(v) for v in all_findings.values())
    if total == 0:
        print("✅ No dangerous exception sinks found.")
        return 0

    blocking_kinds = CRITICAL_KINDS if critical_only else tuple(all_findings.keys())
    blocking_count = sum(len(all_findings.get(k, [])) for k in blocking_kinds)

    if critical_only and blocking_count == 0:
        print(
            f"✅ No CRITICAL exception sinks (write_no_rollback / lying_return). "
            f"({total} INFO findings present — bare_pass / catches_base — not blocking.)"
        )
        return 0

    print(f"❌ EXCEPTION SINK FINDINGS ({total} across {len(all_findings)} categories)\n")
    severities = {
        "write_no_rollback": "🔴 CRITICAL",
        "lying_return": "🔴 CRITICAL",
        "catches_base": "🟠 WARNING",
        "bare_pass": "🟡 INFO",
    }
    for kind in ("write_no_rollback", "lying_return", "catches_base", "bare_pass"):
        items = all_findings.get(kind, [])
        if not items:
            continue
        print(f"{severities.get(kind, kind)} {kind} ({len(items)})")
        # Group by file for readability
        by_file: dict[str, list[Finding]] = defaultdict(list)
        for f in items:
            by_file[f.file].append(f)
        for file, hits in sorted(by_file.items()):
            lines = ", ".join(str(h.line) for h in hits[:8])
            if len(hits) > 8:
                lines += f", +{len(hits) - 8} more"
            print(f"  {file}  [{lines}]")
        print()

    return 1 if blocking_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
