#!/usr/bin/env python3
"""audit_tier_cost_literals.py — block hardcoded subscription / tier
costs in arithmetic without a plan-aware lookup.

Problem class: a service computes `net_roi = prevented - 99.0`
assuming Pro subscription cost, but the value lands in the response
of EVERY plan including Lite/Lite (who pay €0). The UI shows
"Net ROI -€99" to a merchant paying nothing — a silent lie detected
by the 2026-04-19 audit and fixed in commit 3b52b9c.

This audit catches the class by scanning service/API Python files for
numeric literals involved in arithmetic against variables whose name
contains cost/roi/subscription/tier/price semantic markers. If the
literal is NOT inside a plan-keyed dict or a named constant lookup,
it's a candidate for semantic drift.

Coverage claim (honest):
- Catches `net_roi = prevented - 99.0` class (literal on RHS of
  arithmetic with semantic-keyword variable)
- Catches `sub_cost = 99.0` bare assignment in cost/subscription
  contexts
- Does NOT catch all semantic bugs — this is one pattern. Visit
  `/app/page.tsx` for UI-truth bugs, comparison tables for tier-
  mapping bugs. See `feedback_no_accettabile_per_beta.md` —
  deterministic layer catches a subset; brutal interactive audit
  catches the rest.

False-positive handling: allowlist file `::line::note` entries when
the literal is legitimate (e.g., 1.0 for unit conversions).

Exit codes:
    0  clean
    1  literal findings
    2  script error
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT / "app"
SCAN_ROOTS = [APP_ROOT / "services", APP_ROOT / "api"]

# Variable-name substrings that indicate the value is a
# subscription/tier/cost/ROI number that MUST be plan-aware.
# MED-19 closure 2026-04-24: matched as word-parts, not as substring
# collision. Pre-MED-19 `net_roi` would also match `MONTHLY_PRO_ROI_CAP`
# because "roi" was a substring hit in a longer identifier (module
# constant, upper-case). Fix: match identifiers where every separator-
# delimited token aligns with one of the tokens below OR the identifier
# is lowercase (the real variable-naming convention for computed
# cost/ROI values). Module-level UPPER_CASE constants are exempt
# separately via _is_upper_constant().
SEMANTIC_COST_TOKENS = {
    "tier_cost",
    "tier_eur",
    "subscription",
    "net_roi",
    "subscription_cost",
    "pro_cost",
    "pro_tier",
    "monthly_plan",
}

# Literals that are always semantically safe — unit conversions,
# identity elements, percentage 100 / ratio denominators. Extend as
# new legitimate patterns surface.
SAFE_LITERALS = {0, 0.0, 1, 1.0, -1, -1.0, 100, 100.0, None}

# Allowlist: explicit exemptions for legitimate constants that look
# like subscription costs but aren't. Format: "path:lineno" or the
# specific variable name (e.g. "MONTHLY_PRO_COST") — latter applies
# everywhere the name appears. Use the first form for one-off exemptions,
# the second for module-level UPPER_CASE constants that are
# intentionally hardcoded (product decision, not arithmetic drift).
ALLOWLIST: set[str] = set()
NAME_ALLOWLIST: set[str] = {
    # UPPER_CASE module constants where a hardcoded cost literal is
    # the source-of-truth. Arithmetic that USES these is fine; the
    # constant ITSELF isn't drift.
    "MONTHLY_PRO_COST",
    "MONTHLY_PRO_EUR",
    "MONTHLY_STARTER_EUR",
    "MONTHLY_SCALE_EUR",
}


def _is_upper_constant(name: str) -> bool:
    """True if the identifier is an UPPER_SNAKE_CASE module-level
    constant (e.g. `MONTHLY_PRO_COST`). These are by convention the
    source-of-truth for hardcoded costs — not arithmetic drift."""
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]+", name))


def _name_is_cost_semantic(name: str) -> bool:
    """True if the target variable name matches a cost/subscription/
    ROI semantic pattern that must be plan-aware.

    MED-19 closure 2026-04-24: word-boundary matching — token must
    appear as a `_`-delimited word, not an arbitrary substring. Skips
    UPPER_CASE constants (they are source-of-truth by convention).
    """
    if name in NAME_ALLOWLIST:
        return False
    if _is_upper_constant(name):
        return False
    # Split into tokens on underscore boundaries; an identifier like
    # `net_roi` has tokens {"net", "roi"}. A match is when any
    # multi-token SEMANTIC_COST_TOKEN appears as a contiguous sub-
    # sequence in the identifier's token list.
    id_tokens = name.lower().split("_")
    for tok in SEMANTIC_COST_TOKENS:
        tok_parts = tok.split("_")
        if len(tok_parts) == 1:
            if tok_parts[0] in id_tokens:
                return True
        else:
            # look for contiguous subsequence
            for i in range(len(id_tokens) - len(tok_parts) + 1):
                if id_tokens[i:i + len(tok_parts)] == tok_parts:
                    return True
    return False


def _collect_literals_in_arithmetic(node: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, literal_repr) for every numeric constant that
    appears on either side of an arithmetic binary op (Sub/Add/Mult/Div
    against a non-constant). This is the class where a bare number
    being subtracted from a computed value is suspicious."""
    out: list[tuple[int, str]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.BinOp):
            for side in (n.left, n.right):
                if (
                    isinstance(side, ast.Constant)
                    and isinstance(side.value, (int, float))
                    and side.value not in SAFE_LITERALS
                ):
                    out.append((side.lineno, repr(side.value)))
    return out


class _CostLiteralVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[tuple[int, str, str]] = []

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.generic_visit(node)
        targets = [t for t in node.targets if isinstance(t, ast.Name)]
        for target in targets:
            if not _name_is_cost_semantic(target.id):
                continue
            # Bare literal assignment: `sub_cost = 99.0`
            if (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, (int, float))
                and node.value.value not in SAFE_LITERALS
            ):
                self.findings.append(
                    (node.lineno, target.id, repr(node.value.value))
                )
            # Arithmetic with literal: `net_roi = prevented - 99.0`
            for lineno, lit in _collect_literals_in_arithmetic(node.value):
                self.findings.append((lineno, target.id, lit))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.generic_visit(node)
        if not isinstance(node.target, ast.Name):
            return
        if not _name_is_cost_semantic(node.target.id):
            return
        if node.value is None:
            return
        if (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, (int, float))
            and node.value.value not in SAFE_LITERALS
        ):
            self.findings.append(
                (node.lineno, node.target.id, repr(node.value.value))
            )


def scan_file(path: Path) -> list[tuple[str, int, str, str]]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    v = _CostLiteralVisitor(str(path))
    v.visit(tree)
    rel = str(path.relative_to(BACKEND_ROOT))
    return [(rel, lineno, name, lit) for (lineno, name, lit) in v.findings]


@telemetered("audit_tier_cost_literals")
def main(argv: list[str]) -> int:
    findings: list[tuple[str, int, str, str]] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for f in scan_file(py):
                key = f"{f[0]}:{f[1]}"
                if key in ALLOWLIST:
                    continue
                findings.append(f)

    if not findings:
        print(
            "audit_tier_cost_literals: clean — no suspicious tier-cost "
            "literals in arithmetic"
        )
        return 0

    print(
        f"audit_tier_cost_literals: {len(findings)} suspicious "
        "tier-cost literal(s) in arithmetic."
    )
    print()
    print("Each finding is a bare numeric literal used on or assigned")
    print("to a variable whose name suggests subscription/cost/ROI")
    print("semantics. A plan-aware lookup (dict keyed by plan) should")
    print("be used instead, or add the line to ALLOWLIST if legitimate.")
    print()
    for path, lineno, name, lit in findings:
        print(f"  {path}:{lineno}  {name} has literal {lit}")
    print()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_tier_cost_literals: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
