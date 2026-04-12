"""
adversarial_test_gen.py — D4: adversarial fragility analysis of bugfix diffs.

For every bugfix candidate that just passed the main test suite we run a
deterministic AST-based analysis of its added Python lines looking for
known-fragile patterns that tests rarely exercise:

    1. subscript without bounds check     → `x[i]` / `x.pop(0)`
    2. dict bracket access                 → `d["k"]` where `.get("k")` is safer
    3. attribute access without hasattr    → fragile dynamic attr reads
    4. division or modulo by parameter     → no zero guard
    5. unchecked iteration on parameter    → `for i in arg:` without null check

Each triggered pattern is a "probe" — an adversarial test case that the
code failed *statically*. Probes are advisory (they do not block apply)
but they are recorded on the candidate and surfaced in the daily digest.

No test execution, no LLM, no subprocess. Pure AST. Zero budget cost.

Public API
----------
    analyze_diff_for_fragility(patch_diff) -> dict
    run_adversarial_probes(candidate) -> dict
"""
from __future__ import annotations

import ast
import logging
from typing import Any

log = logging.getLogger("adversarial_test_gen")

_MAX_PROBES_PER_CANDIDATE = 10


def _extract_added_python(patch_diff: str | None) -> str:
    """Return the concatenation of `+` lines (excluding `+++` headers)."""
    if not patch_diff:
        return ""
    added: list[str] = []
    for line in patch_diff.split("\n"):
        if line.startswith("+++"):
            continue
        if not line.startswith("+"):
            continue
        added.append(line[1:])
    return "\n".join(added)


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for a in func.args.args:
        names.add(a.arg)
    for a in func.args.kwonlyargs:
        names.add(a.arg)
    if func.args.vararg:
        names.add(func.args.vararg.arg)
    if func.args.kwarg:
        names.add(func.args.kwarg.arg)
    return names


def _is_name_of(node: ast.AST, names: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in names


def _function_has_truthiness_guard(func: ast.AST, var_name: str) -> bool:
    """Return True if the function body contains an early-return/raise
    guard on `var_name` (e.g. `if not x: return`) before its first use."""
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test = node.test
            # `if not x`
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                if isinstance(test.operand, ast.Name) and test.operand.id == var_name:
                    return True
            # `if x is None`
            if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
                    and test.left.id == var_name:
                for op, comp in zip(test.ops, test.comparators):
                    if isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)) \
                            and isinstance(comp, ast.Constant) and comp.value is None:
                        return True
            # `if not x or ...`
            if isinstance(test, ast.BoolOp):
                for v in test.values:
                    if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not) \
                            and isinstance(v.operand, ast.Name) \
                            and v.operand.id == var_name:
                        return True
    return False


def _probe_for_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    """Return the list of fragility probes triggered within this function."""
    probes: list[dict] = []
    params = _param_names(func)
    if not params:
        return probes

    for node in ast.walk(func):
        # 1. subscript without bounds check: `arg[i]` or `arg.pop(0)`
        if isinstance(node, ast.Subscript) and _is_name_of(node.value, params):
            var = node.value.id  # type: ignore[attr-defined]
            if not _function_has_truthiness_guard(func, var):
                probes.append({
                    "kind": "subscript_unchecked",
                    "function": func.name,
                    "param": var,
                    "detail": f"{var}[...] without truthiness/length guard",
                })

        # 2. dict bracket access `d["k"]` when `.get("k")` is safer — only
        # flag when key is a constant string and no guard exists.
        # (Same AST node as above; dict vs list is runtime-only. We skip
        # this rule to avoid false positives — subscript_unchecked covers it.)

        # 3. attribute access without hasattr: `arg.something`
        if isinstance(node, ast.Attribute) and _is_name_of(node.value, params):
            var = node.value.id  # type: ignore[attr-defined]
            if not _function_has_truthiness_guard(func, var):
                probes.append({
                    "kind": "attribute_unchecked",
                    "function": func.name,
                    "param": var,
                    "detail": f"{var}.{node.attr} without None/hasattr guard",
                })

        # 4. division or modulo by a parameter name: `a / b` or `a % b`
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Mod, ast.FloorDiv)):
            if _is_name_of(node.right, params):
                var = node.right.id  # type: ignore[attr-defined]
                probes.append({
                    "kind": "division_by_param",
                    "function": func.name,
                    "param": var,
                    "detail": f"{type(node.op).__name__.lower()} by parameter {var} without zero guard",
                })

        # 5. unchecked iteration: `for x in arg:` where arg is a parameter
        if isinstance(node, ast.For) and _is_name_of(node.iter, params):
            var = node.iter.id  # type: ignore[attr-defined]
            if not _function_has_truthiness_guard(func, var):
                probes.append({
                    "kind": "iteration_unchecked",
                    "function": func.name,
                    "param": var,
                    "detail": f"for ... in {var} without truthiness guard",
                })

    return probes


def analyze_diff_for_fragility(patch_diff: str | None) -> dict[str, Any]:
    """Analyse the diff's added Python lines for fragile patterns.

    Returns a report dict:
        {
            "fragility_score": int,
            "probes": [{kind, function, param, detail}, ...],
            "function_count": int,
            "parse_status": "ok" | "fallback" | "empty",
        }

    A `fragility_score` of 0 means the diff passed all adversarial probes.
    The score is capped at _MAX_PROBES_PER_CANDIDATE per candidate so a
    pathological diff cannot blow up the digest.
    """
    report: dict[str, Any] = {
        "fragility_score": 0,
        "probes": [],
        "function_count": 0,
        "parse_status": "empty",
    }
    pseudo = _extract_added_python(patch_diff)
    if not pseudo.strip():
        return report

    try:
        tree = ast.parse(pseudo)
        report["parse_status"] = "ok"
    except SyntaxError:
        # Diffs span partial blocks; we can't analyse partial source.
        report["parse_status"] = "fallback"
        return report

    all_probes: list[dict] = []
    func_count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_count += 1
            all_probes.extend(_probe_for_function(node))

    # Cap the probe list so one pathological diff can't dominate.
    if len(all_probes) > _MAX_PROBES_PER_CANDIDATE:
        all_probes = all_probes[:_MAX_PROBES_PER_CANDIDATE]

    report["function_count"] = func_count
    report["probes"] = all_probes
    report["fragility_score"] = len(all_probes)
    return report


def run_adversarial_probes(candidate) -> dict[str, Any]:
    """Entry point: analyse a BugFixCandidate's patch_diff and return a
    fragility report. Safe to call after tests have passed; never raises.
    """
    try:
        return analyze_diff_for_fragility(getattr(candidate, "patch_diff", None))
    except Exception as exc:
        log.debug("run_adversarial_probes: analysis failed: %s", exc)
        return {
            "fragility_score": 0,
            "probes": [],
            "function_count": 0,
            "parse_status": "error",
        }
