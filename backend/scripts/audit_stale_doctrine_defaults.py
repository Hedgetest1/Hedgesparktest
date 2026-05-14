#!/usr/bin/env python3
"""audit_stale_doctrine_defaults.py — block hardcoded fallback defaults
next to doctrine-owned keys.

Problem class: a central constant (e.g. `MONTHLY_EUR_CAP = 10.0` in
`app/core/llm_budget.py`) defines doctrine. Callers fetch the runtime
value via `get_usage_summary()` and, defensively, write
`budget.get("monthly_cap_eur", 5.0)`. When doctrine moves (dev cap
went from €5 → €10 on 2026-04-18) those literals silently drift out
of sync and will display the wrong number in every degraded-Redis /
partial-response path. That produced the B2 bug class today.

What this script blocks:
    .get("monthly_cap_eur", 5.0)              → BLOCKED (stale literal)
    .get("monthly_cap_eur", MONTHLY_EUR_CAP)  → OK (named constant)
    .get("monthly_cap_eur", 0.0)              → OK (explicit zero /
                                                   divide-by-zero guard,
                                                   same pattern as
                                                   protection_state.py:63)

Doctrine keys scanned (extend as new ones emerge):
    - monthly_cap_eur
    - monthly_cost_eur
    - monthly_remaining_eur
    - monthly_max_eur

Exit codes:
    0  clean
    1  findings
    2  script error

Strict mode (default): any finding blocks the commit. Add
`--warn-only` to print findings without exit-1.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT / "app"

# Keys that map to runtime-owned doctrine. A literal fallback here is
# stale documentation waiting to drift. Guard defaults (0.0, 0) are
# permitted — they're explicit divide-by-zero safety, not doctrine.
# MED-18 closure 2026-04-24: DOCTRINE_KEYS is a HARDCODED BASELINE; we
# also auto-derive additional keys by scanning `app/core/llm_budget.py`
# for `UPPER_SNAKE_CASE = <number>` module-level constants and treating
# their lowercased name as a doctrine key. Any new constant added to
# llm_budget.py auto-joins the guard set without requiring a manual
# edit to this script.
DOCTRINE_KEYS_BASELINE = frozenset({
    "monthly_cap_eur",
    "monthly_cost_eur",
    "monthly_remaining_eur",
    "monthly_max_eur",
})


def _auto_doctrine_keys() -> set[str]:
    """Scan app/core/llm_budget.py for module-level UPPER_SNAKE_CASE
    numeric constants. Return their names converted to lowercase
    (matches the dict-key style used in get_usage_summary). Fails
    gracefully if the file isn't parseable."""
    p = APP_ROOT / "core" / "llm_budget.py"
    extra: set[str] = set()
    if not p.is_file():
        return extra
    _src = safe_read_text(p)
    if _src is None:
        return extra
    try:
        tree = ast.parse(_src)
    except SyntaxError:
        return extra
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, (int, float)):
            continue
        for t in targets:
            if not isinstance(t, ast.Name):
                continue
            name = t.id
            # Strip leading underscore conventionally used for "private"
            # doctrine values (`_LLM_MAX_MONTHLY_EUR`).
            stripped = name.lstrip("_")
            if stripped == stripped.upper() and len(stripped) > 2:
                extra.add(stripped.lower())
    return extra


DOCTRINE_KEYS = set(DOCTRINE_KEYS_BASELINE) | _auto_doctrine_keys()

# Values that are always safe as a fallback (divide-by-zero guards,
# explicit "nothing" markers). Extend if a new intentional sentinel is
# introduced.
SAFE_NUMERIC_DEFAULTS = {0, 0.0, 1, 1.0, -1, -1.0, None}


def _is_safe_default(node: ast.AST) -> bool:
    """True if the default value is either a named constant (no drift
    risk — moves with the doctrine) or a well-known safety sentinel."""
    # `MONTHLY_EUR_CAP`, `_LLM_MAX_MONTHLY_EUR`, etc.
    if isinstance(node, ast.Name):
        return True
    # `llm_budget.MONTHLY_EUR_CAP`
    if isinstance(node, ast.Attribute):
        return True
    # `0.0`, `0`, `None`
    if isinstance(node, ast.Constant):
        return node.value in SAFE_NUMERIC_DEFAULTS
    # `int(MONTHLY_EUR_CAP)`, `float(MONTHLY_EUR_CAP)` etc.
    if isinstance(node, ast.Call):
        return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[tuple[int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.generic_visit(node)

        # Match foo.get("some_key", <default>)
        if not isinstance(node.func, ast.Attribute):
            return
        if node.func.attr != "get":
            return
        if len(node.args) != 2:
            return

        key_node, default_node = node.args
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            return

        key = key_node.value
        if key not in DOCTRINE_KEYS:
            return

        if _is_safe_default(default_node):
            return

        # It's a literal numeric default against a doctrine key. Stale.
        rendered = ast.unparse(default_node) if hasattr(ast, "unparse") else "<literal>"
        self.findings.append((node.lineno, key, rendered))


def scan_file(path: Path) -> list[tuple[str, int, str, str]]:
    src = safe_read_text(path)
    if src is None:
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    v = _Visitor(str(path))
    v.visit(tree)
    rel = str(path.relative_to(BACKEND_ROOT))
    return [(rel, ln, key, default) for (ln, key, default) in v.findings]


@telemetered("audit_stale_doctrine_defaults")
def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv

    findings: list[tuple[str, int, str, str]] = []
    for py in APP_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        findings.extend(scan_file(py))

    if not findings:
        print("audit_stale_doctrine_defaults: clean — no stale literal fallbacks")
        return 0

    print(
        f"audit_stale_doctrine_defaults: {len(findings)} stale literal "
        f"fallback(s) against doctrine key(s)"
    )
    print()
    print("Fix by replacing the numeric literal with the named doctrine")
    print("constant (e.g. `MONTHLY_EUR_CAP` from `app.core.llm_budget`)")
    print("or by using 0.0 if the intent is a divide-by-zero guard.")
    print()
    for path, lineno, key, default in findings:
        print(f"  {path}:{lineno}  key={key!r}  default={default}")
    print()

    if warn_only:
        print("--warn-only: not failing the audit")
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_stale_doctrine_defaults: script error — {exc}", file=sys.stderr)
        sys.exit(2)
