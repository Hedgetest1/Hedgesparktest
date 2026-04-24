#!/usr/bin/env python3
"""
audit_telegram_destructive_audited.py — every destructive operator command
must write a hash-chained audit_log entry.

Born 2026-04-23 during the Tier-A telegram_agent audit after finding
`_cmd_cleanup_confirm` and `_cmd_cleanup_safe` mutating state (mass
UPDATE on ops_alerts / support_incidents / bugfix_candidates) with
only a `log.warning("AUDIT ...")` line — not a queryable chain entry
per CLAUDE.md §9.3. If the operator's Telegram were ever compromised,
the only trace of destructive cleanup was a syslog string that grep
could lose.

Rule
----
For every `def _cmd_*` function in `telegram_agent.py`, if the body
contains a destructive SQL operation (UPDATE / DELETE / INSERT via
raw text()) OR an ORM mutation (session.add / delete / commit after
attribute mutation), the body must also contain a call to
`write_audit_log(`. Read-only commands (SELECT-only, formatting,
display) are exempt.

Opt-out
-------
A command can opt out with a function-level docstring marker or
preceding comment:

    # audit-log: read-only — <reason>
    def _cmd_xyz(db, ...):
        ...

Intended for commands that only read state + format for the operator.

Scope
-----
Scanned files:
  - app/services/telegram_agent.py

Easy to extend when/if operator commands move to other modules — add
the path to `_SCAN_FILES` below.

Exit codes
----------
  0  clean
  1  one or more destructive commands without audit_log

Usage
-----
    ./scripts/audit_telegram_destructive_audited.py          # report
    ./scripts/audit_telegram_destructive_audited.py --strict # exit 1 on any miss
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from dataclasses import dataclass
from _audit_telemetry_shim import telemetered

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"

_SCAN_FILES = [
    "services/telegram_agent.py",
]

_CMD_PREFIX = "_cmd_"
# 2026-04-23 retro DA: added MERGE (upsert), and TRUNCATE PARTITION
# which the base TRUNCATE match already catches (TRUNCATE alone). DROP
# COLUMN is a subset of ALTER — already covered. This regex catches
# the SQL statement keyword after optional leading whitespace.
_DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(UPDATE|DELETE|INSERT|TRUNCATE|DROP|ALTER|MERGE)\b\s+",
    re.IGNORECASE,
)
_OPT_OUT_MARKER = "audit-log: read-only"


@dataclass
class Finding:
    file: str
    line: int
    function: str
    reason: str


def _function_has_destructive_sql(func: ast.FunctionDef, src: str) -> tuple[bool, str]:
    """True if the function body contains a destructive raw-SQL operation
    (UPDATE/DELETE/INSERT/... inside a text(...) literal). Also returns
    the short SQL snippet that triggered the match for reporting."""
    for node in ast.walk(func):
        # text("UPDATE ...") pattern: Call to `text` with a string arg
        if isinstance(node, ast.Call):
            is_text_fn = (
                (isinstance(node.func, ast.Name) and node.func.id == "text")
                or (isinstance(node.func, ast.Name) and node.func.id == "_text")
                or (isinstance(node.func, ast.Attribute) and node.func.attr in ("text", "_text"))
            )
            if is_text_fn and node.args:
                arg = node.args[0]
                # Extract string value (handles f-strings too)
                sql = ""
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    sql = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    for part in arg.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            sql += part.value
                if sql and _DESTRUCTIVE_SQL_RE.search(sql):
                    # Return first 60 chars of the matching SQL as evidence
                    return True, sql.strip().split("\n")[0][:60]

        # session.delete(...) or session.add(...) — ORM mutations.
        # Narrow to likely-DB-session variable names to exclude
        # redis.delete(key) and other cache/Redis cleanups which are
        # not compliance-destructive.
        # 2026-04-23 retro DA: expanded from `.delete` alone to include
        # ORM bulk-mutation methods and merge (upsert-like operations),
        # which were silently bypassing the compliance audit because
        # they don't match the literal SQL-keyword regex above.
        _DESTRUCTIVE_ORM_METHODS = {
            "delete",
            "merge",
            "bulk_insert_mappings",
            "bulk_update_mappings",
            "bulk_save_objects",
        }
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (node.func.attr in _DESTRUCTIVE_ORM_METHODS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"db", "session", "Session", "_db"}):
                return True, f"{node.func.value.id}.{node.func.attr}(...)"

    return False, ""


def _function_has_audit_log(func: ast.FunctionDef) -> bool:
    """True if the function body contains a call to write_audit_log()."""
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "write_audit_log":
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == "write_audit_log":
                return True
    return False


def _function_has_opt_out(func: ast.FunctionDef, src_lines: list[str]) -> bool:
    """Opt-out marker can live in the docstring OR on the 2 lines above
    the def line."""
    # Check preceding lines
    for offset in (-1, -2, -3):
        idx = func.lineno - 1 + offset
        if 0 <= idx < len(src_lines) and _OPT_OUT_MARKER in src_lines[idx]:
            return True
    # Check docstring
    if (func.body and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
            and isinstance(func.body[0].value.value, str)):
        if _OPT_OUT_MARKER in func.body[0].value.value:
            return True
    return False


def scan_file(path: pathlib.Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        src = path.read_text()
    except (OSError, UnicodeDecodeError):
        return findings

    src_lines = src.splitlines()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        findings.append(Finding(
            file=str(path.relative_to(REPO_ROOT.parent)),
            line=exc.lineno or 0,
            function="<parse>",
            reason=f"syntax error: {exc.msg}",
        ))
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith(_CMD_PREFIX):
            continue
        if _function_has_opt_out(node, src_lines):
            continue

        is_destructive, evidence = _function_has_destructive_sql(node, src)
        if not is_destructive:
            continue

        if _function_has_audit_log(node):
            continue

        findings.append(Finding(
            file=str(path.relative_to(REPO_ROOT.parent)),
            line=node.lineno,
            function=node.name,
            reason=(
                f"destructive SQL detected ({evidence!r}) but no "
                "write_audit_log() call in the function body. "
                "Every operator-triggered state mutation must leave a "
                "hash-chained audit row per CLAUDE.md §9.3."
            ),
        ))

    return findings


@telemetered("audit_telegram_destructive_audited")
def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    all_findings: list[Finding] = []

    for rel_path in _SCAN_FILES:
        path = APP_ROOT / rel_path
        if not path.exists():
            print(
                f"audit_telegram_destructive_audited: note — "
                f"{rel_path} not found (skipping)"
            )
            continue
        all_findings.extend(scan_file(path))

    if not all_findings:
        print(
            f"audit_telegram_destructive_audited: clean — every destructive "
            f"_cmd_* in {len(_SCAN_FILES)} scanned file(s) calls "
            f"write_audit_log (or is annotated read-only)"
        )
        return 0

    print(
        f"audit_telegram_destructive_audited: FAIL — {len(all_findings)} "
        f"destructive operator command(s) without audit_log"
    )
    print()
    for f in all_findings:
        print(f"  {f.file}:{f.line} {f.function}()")
        print(f"    {f.reason}")
        print()

    print("Remediation for each function:")
    print("  A. Add a write_audit_log(...) call in the function body,")
    print("     ideally right after the mutating commit. Pattern:")
    print()
    print("       from app.services.audit import write_audit_log")
    print("       write_audit_log(")
    print('           db,')
    print('           actor_type="telegram_operator",')
    print('           actor_name=str(chat_id or "unknown"),')
    print('           action_type="telegram_<command>",')
    print('           target_type="<surface>",')
    print('           after_state={...counts, scope...},')
    print('           status="completed",')
    print('           metadata={"command": "/<cmd>", ...},')
    print('       )')
    print('       db.commit()')
    print()
    print("  B. If the command only READS state + formats for the")
    print("     operator, annotate the function with a comment above")
    print("     the def line or in the docstring:")
    print()
    print("       # audit-log: read-only — <reason>")
    print("       def _cmd_foo(db, ...): ...")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
