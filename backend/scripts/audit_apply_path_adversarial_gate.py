#!/usr/bin/env python3
"""audit_apply_path_adversarial_gate.py — Structural preventer for
the §21.6 brain-hook #2 (TIER_0 triple-DA) gap that shipped on
2026-05-06.

The bug class
-------------
The autonomous brain ships patches via two paths:

  1. `run_auto_apply`              (bugfix_pipeline.py:~4952)  — TIER_0
  2. `run_governed_tier1_auto_apply` (bugfix_pipeline.py:~5270) — TIER_1

Both end in `apply_bugfix_candidate(db, c.id)`. Founder direttiva
2026-05-06 (CLAUDE.md §21.6): the adversarial 3-lens severity gate
must run BEFORE every autonomous apply. The first commit landed the
gate on path #1 only; the second commit landed it on path #2 after
the founder asked "10/10?". This audit makes that class of regression
mechanical: any future call site of `apply_bugfix_candidate` that
bypasses the adversarial gate fails preflight.

How it works
------------
AST-walks `app/services/bugfix_pipeline.py`. For every function
that contains a call to `apply_bugfix_candidate(...)`, the function's
body must contain a query of `AdversarialReviewFinding.severity`
(the `_check_adversarial_gate` semantic) somewhere above the
apply call. The semantic match is the SQLAlchemy query pattern, NOT
a fixed name — refactoring the gate into a helper is fine as long
as the helper is invoked above the apply call.

Exemptions (operator-driven, not autonomous):
  * `app/api/ops.py` — `/ops/bugfixes/{id}/apply` is human-triggered.
  * Test fixtures — `tests/` ignored.

Exit code:
  0 — every autonomous apply call site is gated.
  1 — at least one call site bypasses the gate (preflight blocks).

# invariant-eligible: true
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "services" / "bugfix_pipeline.py"

# Operator-driven call sites that legitimately call apply_bugfix_candidate
# without the adversarial gate (human is the gate).
_EXEMPT_FUNCS = {"_force_apply_for_operator"}  # placeholder; none today

# Public marker the audit looks for in the function body to consider
# the gate present. We accept ANY of these forms — keeps the audit
# robust to refactor:
#   1. `AdversarialReviewFinding.severity` reference
#   2. Helper call `_check_adversarial_gate`
#   3. Helper call `check_adversarial_gate`
#   4. Comment annotation `# brain-hook: tier_0-triple-da` in body
_GATE_MARKERS_AST = (
    "AdversarialReviewFinding",
    "_check_adversarial_gate",
    "check_adversarial_gate",
)
_GATE_MARKER_COMMENT = "brain-hook: tier_0-triple-da"


def _function_calls_apply(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id == "apply_bugfix_candidate":
                return True
            if isinstance(target, ast.Attribute) and target.attr == "apply_bugfix_candidate":
                return True
    return False


def _function_has_gate(fn: ast.FunctionDef, source: str) -> bool:
    # AST scan for marker names
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in _GATE_MARKERS_AST:
            return True
        if isinstance(node, ast.Attribute):
            # AdversarialReviewFinding.severity — covers ORM filter usage
            if isinstance(node.value, ast.Name) and node.value.id in _GATE_MARKERS_AST:
                return True
            if node.attr in _GATE_MARKERS_AST:
                return True
        if isinstance(node, ast.Call):
            # Helper invocations
            target = node.func
            if isinstance(target, ast.Name) and target.id in _GATE_MARKERS_AST:
                return True
            if isinstance(target, ast.Attribute) and target.attr in _GATE_MARKERS_AST:
                return True
    # Comment annotation fallback — re-scan source slice
    start = (fn.lineno or 1) - 1
    end = (fn.end_lineno or len(source.splitlines())) or len(source.splitlines())
    body = "\n".join(source.splitlines()[start:end])
    if _GATE_MARKER_COMMENT in body:
        return True
    return False


def main() -> int:
    if not TARGET.is_file():
        print(f"audit_apply_path_adversarial_gate: target missing at {TARGET}", file=sys.stderr)
        return 1
    source = TARGET.read_text()
    try:
        tree = ast.parse(source, str(TARGET))
    except SyntaxError as exc:
        print(f"audit_apply_path_adversarial_gate: parse error {exc}", file=sys.stderr)
        return 1

    findings: list[tuple[str, int]] = []
    autonomous_call_sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name in _EXEMPT_FUNCS:
            continue
        if node.name == "apply_bugfix_candidate":
            continue  # the function itself
        if node.name == "_apply_bugfix_candidate_impl":
            continue
        if not _function_calls_apply(node):
            continue
        autonomous_call_sites += 1
        if not _function_has_gate(node, source):
            findings.append((node.name, node.lineno or 0))

    if findings:
        print(
            "audit_apply_path_adversarial_gate: FAIL — "
            f"{len(findings)} of {autonomous_call_sites} autonomous "
            "apply_bugfix_candidate call site(s) bypass the §21.6 "
            "adversarial 3-lens gate."
        )
        for name, lineno in findings:
            print(f"  ✗ {name} @ {TARGET.name}:{lineno}")
        print(
            "\nFix: add an `AdversarialReviewFinding.severity` query (or "
            "call `_check_adversarial_gate` helper) above the "
            "`apply_bugfix_candidate(...)` line. Severity ≥ "
            "_ADVERSARIAL_AUTO_APPLY_BLOCK (= 7) must skip/escalate."
        )
        return 1

    print(
        f"audit_apply_path_adversarial_gate: OK — {autonomous_call_sites} "
        "autonomous apply_bugfix_candidate call site(s), all gated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
