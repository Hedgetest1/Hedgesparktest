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


_DB_SESSION_NAMES = {"db", "session", "self_db", "s", "conn", "connection"}


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
      * db.execute(text("INSERT... | UPDATE... | DELETE..."))

    Writes are NOT:
      * db.execute(text("SELECT ..."))  (read, nothing to roll back)
      * rc.set / rc.setex / rc.delete   (Redis — no transaction)
    """
    has_write = False
    for node in ast.walk(ast.Module(body=try_node.body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not _call_target_is_db(func):
            continue
        name = func.attr.lower()
        if name in {"commit", "flush", "add", "delete"}:
            has_write = True
            break
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
                        break
    if not has_write:
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

    return not (any_handler_rolls_back or any_handler_reraises)


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
            # 2. lying return
            truthy, lit = handler_returns_truthy(h)
            if truthy:
                findings.append(Finding(rel, line, "lying_return", f"except ...: return {lit}"))
            # 3. BaseException catch
            if handler_catches_base(h) and h.type is not None:
                findings.append(Finding(rel, line, "catches_base", "except BaseException"))
        # 4. write without rollback across all handlers
        if try_has_write_without_rollback(node):
            findings.append(Finding(
                rel, node.lineno, "write_no_rollback",
                "try block has write ops but no handler rolls back or re-raises",
            ))

    return findings


def main() -> int:
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
