#!/usr/bin/env python3
"""audit_llm_token_ground_truth.py — block token-count approximations.

Problem class
-------------
`record_usage(module, tokens_used=<X>, ...)` is the sole source of
truth for the monthly LLM-budget rollup surfaced at /ops/llm-budget
and enforced by `check_budget`. On 2026-04-23 a sibling audit found
7 separate call sites that passed `len(text) // 4` (or similar string
-length estimate) instead of the ground-truth `usage.input_tokens +
output_tokens` from the provider response. The estimate drifts 30-50%
from reality on prompts with heavy system/RAG context, silently
inflating budget headroom.

All 7 were closed in the 2026-04-23 sweep. This audit exists so the
class cannot come back.

What it flags
-------------
Any `record_usage(module, tokens_used=<expr>, ...)` call where
`<expr>` matches the approximation pattern:
  - `len(...) // 4`
  - `(len(...) + len(...)) // 4`
  - `len(...) / 4`
  - any call that passes `len(...)` expression AS-IS to tokens_used
    (without an `or` fallback from a ground-truth variable)

Grandfathered
-------------
Patterns of the form `(ground_truth) or (len(x) // 4)` are FINE —
that's the defensive fallback we ship when the provider omits `usage`
entirely. The audit recognizes these by requiring an `or` clause with
a non-len left operand.

Exit code
---------
  0 — clean
  1 — violations found (only with --strict)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "app" / "services"


def _is_approximation_expr(expr: ast.AST) -> bool:
    """Return True if the expression contains a string-length approximation.

    Detects:
      - `len(x) // N` / `len(x) / N`            (direct)
      - `len(x)`                                 (bare)
      - `(X) or len(x) // N`                     (fallback-shape)
      - any nested occurrence of the above inside a larger expression

    Grandfather handling is separate in `_is_grandfathered` — this
    function only answers "is there an approximation anywhere here?".
    """
    # Pattern: `len(x) // 4` or `len(x) / 4`
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.FloorDiv, ast.Div)):
        if _contains_len_call(expr.left):
            return True
    # Pattern: direct `len(x)` passed as tokens_used
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "len":
        return True
    # Pattern: BoolOp(or) with a len-approximation in ANY operand
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
        return any(_is_approximation_expr(v) or _contains_len_call(v) for v in expr.values)
    return False


def _contains_len_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "len":
            return True
    return False


def _is_grandfathered(expr: ast.AST, file_has_usage_parse: bool) -> bool:
    """Recognize `(ground_truth) or (len(x) // 4)` defensive fallback.

    Accepts BoolOp with `or` where the LEFT operand does NOT reduce to
    just `len(...)` — meaning the first preference is a ground-truth
    variable/call and the len-estimate is purely defensive.

    HARDENED (DA-1, 2026-04-23): additionally requires the enclosing
    file to contain a `.get("input_tokens")` / `.get("output_tokens")`
    / `.get("prompt_tokens")` / `.get("completion_tokens")` /
    `.get("total_tokens")` call — empirical evidence that the ground-
    truth variable in the `or` left operand is ACTUALLY sourced from
    the provider's usage struct, not a stale local that happens to be
    named `tokens`. Without this check, a dev could write:

        tokens = 0  # bug
        record_usage(..., tokens_used=tokens or len(text)//4)

    and silently record approximations forever.
    """
    if not isinstance(expr, ast.BoolOp):
        return False
    if not isinstance(expr.op, ast.Or):
        return False
    if not expr.values:
        return False
    left = expr.values[0]
    # left must NOT itself be a bare len-approximation
    if _is_approximation_expr(left):
        return False
    # File must demonstrably parse provider usage struct — otherwise
    # the `or`-left could be any stale variable, not a ground-truth source.
    return file_has_usage_parse


_USAGE_KEY_NAMES = (
    "input_tokens", "output_tokens",
    "prompt_tokens", "completion_tokens",
    "total_tokens",
)


def _file_parses_usage_struct(tree: ast.AST) -> bool:
    """Return True if the file contains at least one `.get("<usage_key>")`
    call with a known usage-key literal. Used to confirm the file
    actually threads ground-truth tokens out of the provider response."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value in _USAGE_KEY_NAMES:
            return True
    # Subscript access `usage["input_tokens"]` also counts
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value in _USAGE_KEY_NAMES:
            return True
    return False


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_no, snippet) for violations in this file."""
    findings: list[tuple[int, str]] = []
    try:
        src = path.read_text()
    except Exception:
        return findings
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return findings

    file_has_usage = _file_parses_usage_struct(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Narrow to `record_usage(...)` calls
        func = node.func
        fname = None
        if isinstance(func, ast.Name):
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = func.attr
        if fname != "record_usage":
            continue
        # Find the tokens_used keyword argument
        for kw in node.keywords:
            if kw.arg != "tokens_used":
                continue
            val = kw.value
            if _is_grandfathered(val, file_has_usage):
                break
            if _is_approximation_expr(val):
                snippet = ast.unparse(val) if hasattr(ast, "unparse") else "<expr>"
                # Different messages for different failure modes help the
                # dev understand WHY the pattern was flagged.
                if isinstance(val, ast.BoolOp) and isinstance(val.op, ast.Or):
                    msg = (
                        f"tokens_used={snippet}  (grandfather-eligible but "
                        "file has no usage-key parse — add "
                        "`usage.get('input_tokens')` etc to prove ground-truth)"
                    )
                else:
                    msg = f"tokens_used={snippet}"
                findings.append((node.lineno, msg))
            break
    return findings


@telemetered("audit_llm_token_ground_truth")
def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, int, str]] = []

    if not SERVICES_DIR.is_dir():
        print(f"✗ services dir missing: {SERVICES_DIR}")
        return 1 if strict else 0

    for py_path in sorted(SERVICES_DIR.glob("*.py")):
        file_hits = _scan_file(py_path)
        for lineno, snippet in file_hits:
            violations.append((py_path, lineno, snippet))

    if violations:
        print(f"✗ LLM token-ground-truth audit — {len(violations)} violations:")
        for path, lineno, snippet in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{lineno}  {snippet}")
        print()
        print("Remediation: thread `input_tokens` + `output_tokens` from the")
        print("provider's usage struct up to the record_usage call, with a")
        print("fallback like: `(in_tok + out_tok) or (len(text) // 4)`")
        print("See app/services/bugfix_pipeline.py _call_llm for the pattern.")
        return 1 if strict else 0

    total_calls = 0
    for py_path in SERVICES_DIR.glob("*.py"):
        try:
            total_calls += py_path.read_text().count("record_usage(")
        except Exception:
            pass
    print(f"✓ every record_usage call uses ground-truth tokens "
          f"(or documented fallback) — scanned {total_calls} call sites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
