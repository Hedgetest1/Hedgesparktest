#!/usr/bin/env python3
"""
audit_safety_check_fail_closed.py — enforce fail-closed semantics on
safety-critical try/except blocks in the self-healing pipeline.

Born 2026-04-23 after the Tier-A reviewer_layer audit surfaced a
silent-skip regression: two blocking-check try/except blocks logged
a warning on exception and returned to normal flow WITHOUT appending
to the blocking list. Net effect: an ImportError in the safety guard
would bypass the guard silently, allowing auto-approve on a patch
that should have been blocked.

Rule
----
For every `try:` block in a safety-critical file, if the TRY body
contains a signal that this is a safety check (append to `blocking` /
`concerns` / explicit `raise`), then the EXCEPT body must also either
raise OR append to the same safety list. An except that only logs
while the try is a safety check = silent-skip = audit failure.

Scope (files scanned)
---------------------
- app/services/reviewer_layer.py    — patch review governance
- app/services/bugfix_pipeline.py   — LLM-driven code mutation
- app/services/promotion_pipeline.py — holdout → promote
- app/services/invariant_monitor.py — runtime invariant checks
- app/services/orchestrator.py      — action execution

Override
--------
A try block can opt out of this check by annotating with a comment
on the try line or the line above:

    # safety-check: fail-open-ok — <reason>
    try:
        ...

Intended for Redis-optional caches and similar where "fail-open on
cache miss" is the correct behaviour. Reason required to document why.

Exit codes
----------
  0  clean (every safety-signal try has fail-closed semantics or opt-out)
  1  one or more silent-skip patterns found

Usage
-----
    ./scripts/audit_safety_check_fail_closed.py          # report
    ./scripts/audit_safety_check_fail_closed.py --strict # exit 1 on any miss
"""
from __future__ import annotations

import ast
import pathlib
import sys
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"

_SAFETY_FILES = [
    "services/reviewer_layer.py",
    "services/bugfix_pipeline.py",
    "services/promotion_pipeline.py",
    "services/invariant_monitor.py",
    "services/orchestrator.py",
]

# Name of the list/variable whose `.append()` marks a safety-check try body.
_SAFETY_LIST_NAMES = {"blocking", "concerns", "blocking_concerns"}

_OPT_OUT_MARKER = "safety-check: fail-open-ok"


@dataclass
class Finding:
    file: str
    line: int
    reason: str


def _is_safety_list_mutation(node: ast.AST) -> bool:
    """True if `node` is a signal that a safety list has been mutated.

    Recognizes:
        <list>.append(x)               (original)
        <list>.extend([x, y])          (2026-04-23 retro DA — extend variant)
        <list> += [x]                  (AugAssign list concat)
        <set> |= {x}                   (AugAssign set union, 2026-04-23 retro DA)
        <set>.add(x)                   (set add, 2026-04-23 retro DA)
        <set>.update({x, y})           (set update, 2026-04-23 retro DA)
    """
    # Method-call patterns
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            if node.func.value.id in _SAFETY_LIST_NAMES:
                if node.func.attr in {"append", "extend", "add", "update"}:
                    return True
    # AugAssign patterns (e.g. `concerns |= {x}` or `blocking += [y]`)
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        if node.target.id in _SAFETY_LIST_NAMES:
            if isinstance(node.op, (ast.Add, ast.BitOr)):
                return True
    return False


def _try_body_is_safety_check(body: list[ast.stmt]) -> bool:
    """True if try body contains a safety-list mutation OR a raise."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if _is_safety_list_mutation(node):
            return True
        if isinstance(node, ast.Raise):
            return True
    return False


def _except_body_is_fail_closed(body: list[ast.stmt]) -> bool:
    """True if except body mutates the safety list OR raises."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if _is_safety_list_mutation(node):
            return True
        if isinstance(node, ast.Raise):
            return True
    return False


def _has_opt_out_on_line(src_lines: list[str], try_lineno: int) -> bool:
    """Check the try line AND the six lines above for the opt-out marker.

    2026-04-23 retro DA: widened annotation window from 2 to 6 lines
    because an author may place a multi-line rationale comment above
    the try block:
        # safety-check: fail-open-ok —
        # the Redis probe can fail transiently during deploy; we log
        # and continue because ...
        try: ...
    The prior 2-line window missed the marker when the rationale was
    on the earliest of a multi-line comment.
    """
    for offset in range(0, -7, -1):
        idx = try_lineno - 1 + offset
        if 0 <= idx < len(src_lines):
            if _OPT_OUT_MARKER in src_lines[idx]:
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
            reason=f"syntax error: {exc.msg}",
        ))
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not _try_body_is_safety_check(node.body):
            continue
        if _has_opt_out_on_line(src_lines, node.lineno):
            continue

        # Check each except handler: every one must be fail-closed.
        for handler in node.handlers:
            if not _except_body_is_fail_closed(handler.body):
                findings.append(Finding(
                    file=str(path.relative_to(REPO_ROOT.parent)),
                    line=handler.lineno,
                    reason=(
                        "try body contains safety-signal (blocking.append "
                        "or raise) but except body only logs — silent-skip. "
                        "Add blocking.append(...) in the except OR re-raise "
                        "OR annotate with `# safety-check: fail-open-ok — <reason>`"
                    ),
                ))

    return findings


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    all_findings: list[Finding] = []

    for rel_path in _SAFETY_FILES:
        path = APP_ROOT / rel_path
        if not path.exists():
            # Missing file: warn but don't fail — file may have been renamed
            print(
                f"audit_safety_check_fail_closed: note — "
                f"{rel_path} not found (skipping)"
            )
            continue
        all_findings.extend(scan_file(path))

    if not all_findings:
        print(
            f"audit_safety_check_fail_closed: clean — every safety-check "
            f"try/except in {len(_SAFETY_FILES)} scanned file(s) is "
            f"fail-closed or opt-out annotated"
        )
        return 0

    print(
        f"audit_safety_check_fail_closed: FAIL — {len(all_findings)} "
        f"silent-skip safety-check pattern(s) found"
    )
    print()
    by_file: dict[str, list[Finding]] = {}
    for f in all_findings:
        by_file.setdefault(f.file, []).append(f)
    for fname, finds in sorted(by_file.items()):
        print(f"  {fname}:")
        for f in finds:
            print(f"    line {f.line}: {f.reason}")
    print()
    print("Remediation:")
    print("  Each flagged except block is a silent-skip: when the safety")
    print("  check itself fails to run (ImportError, DB error, etc.), the")
    print("  check silently skipped and the entity may be approved/applied")
    print("  without verification.")
    print()
    print("  Fix option A (fail-closed — preferred): in the except block,")
    print("  append a blocking concern that says 'safety check failed —")
    print("  manual review required'.")
    print()
    print("  Fix option B (re-raise): if the caller is expected to handle")
    print("  the error, re-raise instead of swallowing.")
    print()
    print("  Fix option C (intentional fail-open): annotate with")
    print("    # safety-check: fail-open-ok — <reason>")
    print("  on the try line or up to 2 lines above. Reason required.")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
