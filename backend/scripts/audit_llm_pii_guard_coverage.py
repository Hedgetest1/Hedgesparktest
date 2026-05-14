#!/usr/bin/env python3
"""
audit_llm_pii_guard_coverage.py — assert every direct LLM call site
passes user/merchant content through the PII guard.

Born 2026-04-23 during the Tier-A agent audit after 4 sites were
found shipping httpx.post to anthropic/openai endpoints WITHOUT
calling llm_pii_guard.assert_clean / check_for_pii on the prompt.

Rule (CLAUDE.md §8.1):
  "Every LLM call MUST be flagged in advance with: estimated cost at
   10k merchants, deterministic alternative considered, fallback when
   budget exhausted. ... Runtime PII guard (app/core/llm_pii_guard.py):
   deterministic regex scanner wired into every LLM call site."

This audit enforces the "wired into every LLM call site" clause
statically at commit time.

Heuristic
---------
For every .py file under app/services/ and app/core/ that contains
a reference to api.anthropic.com or api.openai.com:
  - If the file also contains at least one reference to
    `llm_pii_guard` (import) OR `assert_clean` OR `check_for_pii`
    → treat as guarded.
  - Otherwise → flag as missing coverage.

False-positive tolerance: a file can opt-out by a top-level comment
`# llm_pii_guard_audit: synthetic-only  — reason`. Intended for
benchmark/eval corpora that are provably synthetic.

Exit codes
----------
  0  clean (all LLM call sites have a PII guard reference)
  1  one or more sites missing coverage

Usage
-----
    ./scripts/audit_llm_pii_guard_coverage.py          # report
    ./scripts/audit_llm_pii_guard_coverage.py --strict # exit 1 on any miss
"""
from __future__ import annotations

import pathlib
import re
import sys
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"
SCAN_DIRS = ["services", "core", "api", "workers"]

# Multi-provider URL patterns (2026-04-23 retro DA sweep). Keep in sync
# with audit_llm_truncation_rejection + audit_llm_http_timeout vendor
# coverage.
_LLM_URL_PATTERNS = [
    re.compile(r"api\.anthropic\.com"),
    re.compile(r"api\.openai\.com"),
    re.compile(r"api\.mistral\.ai"),
    re.compile(r"generativelanguage\.googleapis\.com"),
    re.compile(r"ai\.google\.dev"),
]

_PII_GUARD_PATTERNS = [
    re.compile(r"\bllm_pii_guard\b"),
    re.compile(r"\bassert_clean\b"),
    re.compile(r"\bcheck_for_pii\b"),
]

_OPT_OUT_PATTERN = re.compile(r"#\s*llm_pii_guard_audit:\s*synthetic-only", re.IGNORECASE)


def scan_file(path: pathlib.Path) -> dict:
    text = safe_read_text(path)
    if text is None:
        return {"error": "unreadable"}

    has_llm_call = any(p.search(text) for p in _LLM_URL_PATTERNS)
    if not has_llm_call:
        return {"llm_call": False}

    has_pii_guard = any(p.search(text) for p in _PII_GUARD_PATTERNS)
    opt_out = bool(_OPT_OUT_PATTERN.search(text))
    return {
        "llm_call": True,
        "pii_guard": has_pii_guard,
        "opt_out": opt_out,
    }


@telemetered("audit_llm_pii_guard_coverage")
def main(argv: list[str]) -> int:
    strict = "--strict" in argv

    missing: list[pathlib.Path] = []
    opted_out: list[pathlib.Path] = []
    guarded: list[pathlib.Path] = []

    for subdir in SCAN_DIRS:
        base = APP_ROOT / subdir
        if not base.exists():
            continue
        for py in sorted(base.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            result = scan_file(py)
            if not result.get("llm_call"):
                continue
            if result.get("pii_guard"):
                guarded.append(py)
            elif result.get("opt_out"):
                opted_out.append(py)
            else:
                missing.append(py)

    total_llm_sites = len(missing) + len(opted_out) + len(guarded)

    if not missing:
        print(
            f"audit_llm_pii_guard_coverage: clean — "
            f"{len(guarded)} guarded + {len(opted_out)} opt-out = "
            f"{total_llm_sites}/{total_llm_sites} LLM sites covered"
        )
        if opted_out:
            print("Opt-outs (synthetic-only annotated):")
            for p in opted_out:
                print(f"  {p.relative_to(REPO_ROOT.parent)}")
        return 0

    print(
        f"audit_llm_pii_guard_coverage: FAIL — {len(missing)} LLM call "
        f"site(s) WITHOUT PII guard"
    )
    print()
    print("Missing PII guard (expected: import llm_pii_guard + call")
    print("assert_clean/check_for_pii on the prompt BEFORE httpx.post):")
    for p in missing:
        print(f"  {p.relative_to(REPO_ROOT.parent)}")
    print()
    print("Remediation for each file:")
    print("  1. Add at the top of the _call_* function:")
    print('     from app.core.llm_pii_guard import assert_clean, LLMPayloadViolation')
    print('     try:')
    print('         assert_clean(prompt, context="<module_name>")')
    print('     except LLMPayloadViolation as exc:')
    print('         log.warning("<module>: pii_guard blocked: %s", exc)')
    print('         return None  # same path as budget-exhaustion')
    print()
    print("  2. If the prompt is provably synthetic (no merchant/user data),")
    print("     add at the top of the file:")
    print("       # llm_pii_guard_audit: synthetic-only — <reason>")
    return 1 if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
