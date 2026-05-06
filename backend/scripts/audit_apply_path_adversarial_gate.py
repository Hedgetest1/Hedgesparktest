#!/usr/bin/env python3
"""audit_apply_path_adversarial_gate.py — Structural preventer for
the §21.6 brain-hook #2 (TIER_0 triple-DA) gap that shipped on
2026-05-06.

The bug class
-------------
The autonomous brain ships patches via two paths today:

  1. `run_auto_apply`              (bugfix_pipeline.py)  — TIER_0
  2. `run_governed_tier1_auto_apply` (bugfix_pipeline.py) — TIER_1

Both end in `apply_bugfix_candidate(db, c.id)`. Founder direttiva
2026-05-06 (CLAUDE.md §21.6): the adversarial 3-lens severity gate
must run BEFORE every autonomous apply. The first commit landed the
gate on path #1 only; the second commit landed it on path #2 after
the founder asked whether the work was honestly at the bar. This
audit makes that class of regression mechanical.

Cross-module coverage (2026-05-06 upgrade)
-------------------------------------------
The audit AST-walks EVERY .py file under `app/` that imports
`apply_bugfix_candidate` (not just bugfix_pipeline.py). For each
function calling apply_bugfix_candidate, we classify:

  * **Operator** (human is the gate, exempt) — function body
    contains audit_log markers `actor_type="human"`,
    `approval_mode="human_approved"`, or `actor_name=`-with-
    operator-keyword (telegram_operator, ops_admin, etc.). Today
    these are `app/api/ops.py::apply_bugfix` and
    `app/services/telegram_agent.py::cmd_apply`.

  * **Autonomous** (must be gated) — anything else. Verify the
    function body invokes `_check_adversarial_gate` (DRY helper),
    `AdversarialReviewFinding.severity` (legacy inline form), or
    carries the comment marker `# brain-hook: tier_0-triple-da`.

Future-proofing: a new caller added in any module is detected by
the import-scan + AST classification — no audit refactor needed
unless the apply function's NAME changes (covered by the Renaming
mitigation below).

Renaming mitigation
-------------------
If `apply_bugfix_candidate` is ever renamed, this audit's
`_TARGET_FUNC_NAME` constant must change in the same commit.
The constant lives in source so a rename refactor surfaces it via
grep; a future drift would still fire the audit (zero matches → 0
autonomous call sites → trivially OK, but ALSO INFO line emitted
so operator notices).

Exit codes
----------
  0 — every autonomous apply call site is gated.
  1 — at least one call site bypasses the gate (preflight blocks).

# invariant-eligible: true
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

_TARGET_FUNC_NAME = "apply_bugfix_candidate"

# Operator classifier (post-FINDING-3 hardening, 2026-05-06):
# AST-verify a real `write_audit_log(..., actor_type="human", ...)`
# Call node OR `actor_type="human"` keyword in any Call. Reject
# string-substring matches in comments/docstrings/dead literals.
# The previous text-only check was fakeable: a comment containing
# `# actor_type="human"` would silently exempt an autonomous path.
_OPERATOR_KWARG_NAME = "actor_type"
_OPERATOR_KWARG_VALUE = "human"
_OPERATOR_APPROVAL_KWARG_NAME = "approval_mode"
_OPERATOR_APPROVAL_KWARG_VALUE = "human_approved"

# Markers detected anywhere inside the function body that classify
# the function as carrying the adversarial gate (any of the 4 forms
# is accepted — robust to refactor):
#   1. AdversarialReviewFinding ORM reference (legacy inline form)
#   2. _check_adversarial_gate helper Call
#   3. check_adversarial_gate helper Call (public alias if added)
#   4. Comment annotation `# brain-hook: tier_0-triple-da`
_GATE_MARKERS_AST = (
    "AdversarialReviewFinding",
    "_check_adversarial_gate",
    "check_adversarial_gate",
)
_GATE_MARKER_COMMENT = "brain-hook: tier_0-triple-da"

# Functions that ARE the apply implementation itself — recursion in
# the wrapper is fine, no gate needed.
_SELF_FUNC_NAMES = {
    _TARGET_FUNC_NAME,
    f"_{_TARGET_FUNC_NAME}_impl",
}


def _module_imports_target(tree: ast.AST) -> bool:
    """True if the module imports apply_bugfix_candidate from
    bugfix_pipeline (or has a direct re-export reference)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "bugfix_pipeline" in node.module:
                for alias in node.names:
                    if alias.name == _TARGET_FUNC_NAME or alias.name == "*":
                        return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name and "bugfix_pipeline" in alias.name:
                    return True
    return False


def _function_calls_apply(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id == _TARGET_FUNC_NAME:
                return True
            if isinstance(target, ast.Attribute) and target.attr == _TARGET_FUNC_NAME:
                return True
    return False


def _function_body_text(fn: ast.FunctionDef, source: str) -> str:
    start = (fn.lineno or 1) - 1
    end = (fn.end_lineno or len(source.splitlines())) or len(source.splitlines())
    return "\n".join(source.splitlines()[start:end])


def _kwarg_constant_value(call: ast.Call, name: str) -> str | None:
    """Return the string value of `<name>=<const>` keyword arg of a
    Call, or None if the kwarg is absent / non-constant / non-string.
    Comments and docstrings do not appear as Call kwargs in the AST,
    so this is immune to substring-spoofing."""
    for kw in call.keywords:
        if kw.arg != name:
            continue
        v = kw.value
        # Python 3.8+: ast.Constant supersedes ast.Str
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        # Legacy fallback (ast.Str pre-3.8) — defensive only
        if hasattr(ast, "Str") and isinstance(v, ast.Str):  # type: ignore[attr-defined]
            return v.s  # type: ignore[attr-defined]
    return None


def _is_operator_function(fn: ast.FunctionDef, source: str) -> bool:
    """AST-verify the function contains a real Call node carrying
    actor_type="human" OR approval_mode="human_approved" kwarg.

    Pre-2026-05-06 this used substring matching on the function body
    text, which was fakeable via comment / docstring / dead string
    literal. External CTO audit FINDING 3 caught the gap — comments
    do not produce Call nodes in the AST so this rewrite is immune.
    """
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        v = _kwarg_constant_value(node, _OPERATOR_KWARG_NAME)
        if v == _OPERATOR_KWARG_VALUE:
            return True
        v = _kwarg_constant_value(node, _OPERATOR_APPROVAL_KWARG_NAME)
        if v == _OPERATOR_APPROVAL_KWARG_VALUE:
            return True
    return False


def _function_has_gate(fn: ast.FunctionDef, source: str) -> bool:
    # AST scan for marker names
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _GATE_MARKERS_AST:
            return True
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in _GATE_MARKERS_AST:
                return True
            if node.attr in _GATE_MARKERS_AST:
                return True
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id in _GATE_MARKERS_AST:
                return True
            if isinstance(target, ast.Attribute) and target.attr in _GATE_MARKERS_AST:
                return True
    # Comment annotation fallback
    if _GATE_MARKER_COMMENT in _function_body_text(fn, source):
        return True
    return False


def main() -> int:
    if not APP.is_dir():
        print(f"audit_apply_path_adversarial_gate: app dir missing at {APP}", file=sys.stderr)
        return 1

    findings: list[tuple[Path, str, int]] = []  # (file, fn_name, lineno)
    operator_skips: list[tuple[Path, str, int]] = []
    autonomous_count = 0
    files_scanned = 0

    for pyfile in APP.rglob("*.py"):
        files_scanned += 1
        try:
            source = pyfile.read_text(encoding="utf-8")
        except Exception:
            continue
        # Quick reject: file does not even mention the target name
        if _TARGET_FUNC_NAME not in source:
            continue
        try:
            tree = ast.parse(source, str(pyfile))
        except SyntaxError:
            continue
        # Importer check — the apply function lives in
        # bugfix_pipeline; only modules that import it can call it.
        # The pipeline file itself is the implementation; recursion
        # in the wrapper is fine.
        is_pipeline_self = pyfile.name == "bugfix_pipeline.py"
        if not (is_pipeline_self or _module_imports_target(tree)):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in _SELF_FUNC_NAMES:
                continue
            if not _function_calls_apply(node):
                continue
            rel = pyfile.relative_to(ROOT)
            if _is_operator_function(node, source):
                operator_skips.append((rel, node.name, node.lineno or 0))
                continue
            autonomous_count += 1
            if not _function_has_gate(node, source):
                findings.append((rel, node.name, node.lineno or 0))

    # Reporting
    if findings:
        print(
            "audit_apply_path_adversarial_gate: FAIL — "
            f"{len(findings)} of {autonomous_count} autonomous "
            f"{_TARGET_FUNC_NAME} call site(s) bypass the §21.6 "
            "adversarial 3-lens gate."
        )
        for rel, name, lineno in findings:
            print(f"  ✗ {rel}::{name} @ {lineno}")
        if operator_skips:
            print(f"\n  Operator-driven exempt sites ({len(operator_skips)}):")
            for rel, name, lineno in operator_skips:
                print(f"    · {rel}::{name} @ {lineno}")
        print(
            "\nFix: add an `AdversarialReviewFinding.severity` query, "
            "call `_check_adversarial_gate(db, candidate_id)`, or add "
            "a `# brain-hook: tier_0-triple-da` annotation in the "
            "function body above the apply call. Severity ≥ "
            "_ADVERSARIAL_AUTO_APPLY_BLOCK (= 7) must skip / escalate.\n"
            "Operator-driven exempt: ensure the function records "
            "actor_type=\"human\" / approval_mode=\"human_approved\" "
            "via write_audit_log so the human acts as the gate."
        )
        return 1

    summary = (
        f"audit_apply_path_adversarial_gate: OK — {autonomous_count} "
        f"autonomous {_TARGET_FUNC_NAME} call site(s) all gated; "
        f"{len(operator_skips)} operator-driven exempt; {files_scanned} "
        "py file(s) scanned."
    )
    print(summary)
    if operator_skips:
        for rel, name, lineno in operator_skips:
            print(f"  · operator-exempt: {rel}::{name} @ {lineno}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
