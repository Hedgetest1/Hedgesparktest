#!/usr/bin/env python3
"""audit_llm_per_merchant_budget_gate.py — merchant-scoped LLM calls must gate.

Problem class
-------------
Module-level LLM calls (bugfix_proposal, monthly_opus_audit, etc.) run
on a worker schedule and charge the GLOBAL monthly cap via
`check_budget("<module>")`. They have no merchant context — fine.

Merchant-facing LLM calls (chatbot_llm_fallback, analytics_assistant)
are triggered by a specific merchant and charged against BOTH the
global cap AND the merchant's plan budget. Without the per-merchant
gate, one abusive merchant can exhaust the global daily cap and
starve every other merchant's LLM feature.

On 2026-04-23 a sweep found chatbot_llm_fallback had the gate
(`can_charge_merchant`) but analytics_assistant did not. Wired the
gate, added this preflight so the class never regresses.

What this audit checks
----------------------
Scans app/services/ for any module that:
  1. Takes a `shop`/`shop_domain` parameter in a function signature
  2. Makes an LLM API call (api.anthropic.com or api.openai.com) inside
     the same file

AND requires that file to call `can_charge_merchant(...)` somewhere in
the module. Failing that → violation.

Exit code
---------
  0 — clean
  1 — ungated merchant-scoped LLM call (--strict)
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "app" / "services"

_LLM_URL_RE = re.compile(r"api\.(anthropic|openai)\.com")
_MERCHANT_PARAM_NAMES = {"shop", "shop_domain", "merchant", "merchant_domain"}

# Files that implement their own per-merchant gate via a different
# mechanism (e.g. Redis-backed atomic count limiter with a merchant-
# scoped key) — equivalent protection, different primitive. Each
# entry must have an inline rationale when added.
ALTERNATIVE_GATE_ALLOWLIST = {
    # nudge_composer uses `_check_and_increment_budget` + `_budget_key`
    # with Redis key `hs:ai_budget:{shop}:{YYYY-MM-DD}` atomically
    # INCR'd and compared against `_DAILY_BUDGET` (50/day/shop). This
    # is a COUNT gate (not an EUR gate) but is more stringent in
    # practice (50 × €0.001 Haiku = €0.05/day cap, below even the
    # free-plan monthly EUR cap). Functionally equivalent protection.
    "app/services/nudge_composer.py",
}


def _file_has_llm_call(src: str) -> bool:
    return bool(_LLM_URL_RE.search(src))


def _file_has_merchant_gate(src: str) -> bool:
    return "can_charge_merchant" in src


def _has_merchant_scoped_function(tree: ast.AST) -> tuple[bool, list[str]]:
    """Return (yes, list of function names) that take a merchant-identifying
    parameter. Walks top-level and nested function defs."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        all_args = list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs)
        for arg in all_args:
            if arg.arg in _MERCHANT_PARAM_NAMES:
                hits.append(node.name)
                break
    return (bool(hits), hits)


def _scan_file(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        src = path.read_text()
    except Exception:
        return findings
    if not _file_has_llm_call(src):
        return findings
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return findings

    has_merchant_scope, functions = _has_merchant_scoped_function(tree)
    if not has_merchant_scope:
        return findings  # module-level LLM, not merchant-scoped — OK
    if _file_has_merchant_gate(src):
        return findings  # already calls can_charge_merchant — OK
    rel = str(path.relative_to(REPO_ROOT))
    if rel in ALTERNATIVE_GATE_ALLOWLIST:
        return findings  # documented alternative gate mechanism

    findings.append(
        f"merchant-scoped LLM call (functions: {functions[:3]}) but file "
        f"never calls can_charge_merchant — global cap alone can be "
        f"eaten by a single abusive merchant"
    )
    return findings


def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, str]] = []

    if not SERVICES_DIR.is_dir():
        print(f"✗ services dir missing: {SERVICES_DIR}")
        return 1 if strict else 0

    scanned = 0
    merchant_scoped = 0
    for py_path in sorted(SERVICES_DIR.glob("*.py")):
        scanned += 1
        for finding in _scan_file(py_path):
            violations.append((py_path, finding))
            merchant_scoped += 1

    if violations:
        print(f"✗ per-merchant LLM budget gate — {len(violations)} ungated modules:")
        for path, desc in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}")
            print(f"    → {desc}")
        print()
        print("Remediation: inside the merchant-scoped entry function,")
        print("before gathering context or calling the LLM, add:")
        print("  from app.core.llm_budget import can_charge_merchant")
        print("  ok, reason = can_charge_merchant(db, shop_domain, _ESTIMATED_COST_EUR)")
        print("  if not ok: return <fallback>")
        print("See chatbot_llm_fallback._should_use_llm for reference.")
        return 1 if strict else 0

    print(f"✓ every merchant-scoped LLM call passes through can_charge_merchant "
          f"— scanned {scanned} services")
    return 0


if __name__ == "__main__":
    sys.exit(main())
