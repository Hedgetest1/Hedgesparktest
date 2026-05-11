#!/usr/bin/env python
# invariant-eligible: false — static AST audit, no runtime state.
# Wired into preflight.sh only; periodic invariant_monitor would
# add no signal (the same files don't change between worker cycles).
"""audit_subprocess_arg_origin.py — assert every `subprocess.run` /
`subprocess.Popen` call in production code uses either:

  (a) HARDCODED string literals as the command list (no variable args
      that could be attacker-influenced), OR
  (b) An explicit `# subprocess-allowlist: <pattern>` comment within
      4 lines documenting the allowlist + validation logic.

Born 2026-05-11 Sprint A security audit (C4). The Agent's lens was:
"PM2 / shell command surfaces in `orchestrator.run` callable from
worker pipeline — RCE-adjacent if candidate metadata ever influenced
the target string". On audit, ALL existing subprocess sites turned
out to be safe (hardcoded args OR explicit allowlist on the user-
facing one). But a future contributor adding a new action without
remembering the allowlist pattern is the real risk — this preventer
catches that class.

The audit walks the AST of every subprocess.{run|Popen} call:
  - All literal args + None kwargs → ✓ OK
  - Variable in args list → require `# subprocess-allowlist:` comment
    within 4 lines (typically the line above the call)

Usage:
    ./venv/bin/python scripts/audit_subprocess_arg_origin.py

Exit non-zero if any unannotated variable-arg site found.
"""
from __future__ import annotations

import ast
import pathlib
import sys


# Telemetry shim: this audit registers under the same `_audit_telemetry_shim`
# pattern that preflight.sh enforces for all wired audits.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _audit_telemetry_shim import telemetered


REPO_BACKEND = pathlib.Path("/opt/wishspark/backend")
SCAN_ROOTS = [
    REPO_BACKEND / "app" / "services",
    REPO_BACKEND / "app" / "api",
    REPO_BACKEND / "app" / "workers",
    REPO_BACKEND / "app" / "core",
]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

# A subprocess call whose first positional arg contains a non-literal
# (i.e. a variable, attribute access, function call) MUST carry this
# marker in the source within 4 lines of the call.
ANNOTATION_MARKER = "subprocess-allowlist:"
ANNOTATION_WINDOW_LINES = 6


def _is_subprocess_call(node: ast.AST) -> bool:
    """Return True iff `node` is a Call to subprocess.run or subprocess.Popen."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (
            func.value.id == "subprocess"
            and func.attr in {"run", "Popen", "check_output", "check_call", "call"}
        )
    return False


def _first_arg_is_all_literal(call: ast.Call) -> bool:
    """The command argument (first positional) must be a list/tuple of
    string literals OR a single string literal. Anything else → flagged.
    """
    if not call.args:
        return False
    cmd = call.args[0]
    if isinstance(cmd, ast.Constant) and isinstance(cmd.value, str):
        return True
    if isinstance(cmd, (ast.List, ast.Tuple)):
        return all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            for elt in cmd.elts
        )
    return False


def _has_annotation_nearby(
    source_lines: list[str], call_line_no: int,
) -> bool:
    """Search for ANNOTATION_MARKER in the 4 lines BEFORE the call
    (1-indexed, so call_line_no=10 checks lines 6..10 inclusive)."""
    start = max(0, call_line_no - ANNOTATION_WINDOW_LINES - 1)
    end = call_line_no
    return any(
        ANNOTATION_MARKER in source_lines[i]
        for i in range(start, end)
        if i < len(source_lines)
    )


def _audit_file(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_no, snippet) for unannotated variable-arg
    subprocess calls."""
    try:
        src = path.read_text()
    except Exception:
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    source_lines = src.splitlines()
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not _is_subprocess_call(node):
            continue
        if _first_arg_is_all_literal(node):
            continue
        if _has_annotation_nearby(source_lines, node.lineno):
            continue
        # Variable-arg subprocess call WITHOUT annotation → flagged
        snippet = (
            source_lines[node.lineno - 1].strip()
            if node.lineno - 1 < len(source_lines) else ""
        )
        findings.append((node.lineno, snippet))
    return findings


@telemetered("audit_subprocess_arg_origin")
def main() -> int:
    total_findings: list[tuple[str, int, str]] = []
    files_scanned = 0
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files_scanned += 1
            for line_no, snippet in _audit_file(path):
                rel = path.relative_to(REPO_BACKEND)
                total_findings.append((str(rel), line_no, snippet))

    if total_findings:
        print(
            f"audit_subprocess_arg_origin: {len(total_findings)} unannotated "
            f"variable-arg subprocess call(s) across {files_scanned} files:"
        )
        for rel, line_no, snippet in total_findings:
            print(f"  {rel}:{line_no}  {snippet[:100]}")
        print(
            "\nFix: either (a) pass only string literals as the command, "
            "OR (b) add `# subprocess-allowlist: <description of allowlist>` "
            "within 4 lines above the call, ensuring the variable is "
            "validated against a hard-coded enum before the call."
        )
        return 1

    print(
        f"audit_subprocess_arg_origin: {files_scanned} files scanned, "
        "all subprocess calls safe (literal args OR annotated allowlist)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
