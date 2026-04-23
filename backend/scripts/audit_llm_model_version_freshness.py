#!/usr/bin/env python3
"""audit_llm_model_version_freshness.py — block stale Claude model strings.

Problem class
-------------
Claude model identifiers drift over time. Examples:
  - `claude-sonnet-4-20250514` → superseded by `claude-sonnet-4-6`
  - `claude-opus-4-20250514`   → superseded by `claude-opus-4-7`
  - `claude-haiku-4-5-20251001` (current)

CLAUDE.md operating principle: "default to the latest and most
capable Claude models". On 2026-04-23 a sweep found 6 files hardcoded
to the earlier -20250514 strings well after Sonnet 4.6 / Opus 4.7
became canonical. The API still accepts them (backward-compat), so no
runtime error — just quietly-suboptimal output + stale cost tables.

This audit maintains a CANONICAL_MODELS allowlist sourced from
CLAUDE.md. Any Claude model identifier in `app/` that is NOT in the
allowlist (and not tagged as a legacy alias) trips the audit.

Canonical list (2026-04-23)
---------------------------
  claude-opus-4-7
  claude-sonnet-4-6
  claude-haiku-4-5-20251001

Exit code
---------
  0 — clean
  1 — stale model string found (--strict)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

# Keep in sync with CLAUDE.md model lineup.
CANONICAL_MODELS = {
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}

# Files that legitimately reference LEGACY model strings as historical
# aliases — cost-attribution tables, migration breadcrumbs, etc. They
# opt out of the audit via a per-file path allowlist.
LEGACY_ALIAS_ALLOWLIST = {
    # cost table keeps old keys so historical llm_daily_usage rows
    # still resolve a cost when the summary walks Redis counters.
    str((APP_DIR / "services" / "system_summary.py").relative_to(REPO_ROOT)),
    # Redis-counter-key compatibility: llm_budget's _COST_PER_1K_TOKENS
    # maps model-name → cost. Pre-upgrade Redis rows are keyed on the
    # old strings; dropping them would break cost rollup on historical
    # periods. Each legacy entry is annotated in source.
    str((APP_DIR / "core" / "llm_budget.py").relative_to(REPO_ROOT)),
}

_CLAUDE_MODEL_RE = re.compile(r'["\'](claude-[a-z0-9\-]+)["\']')


def _scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        src = path.read_text()
    except Exception:
        return findings

    for i, line in enumerate(src.splitlines(), start=1):
        # Skip comments (cheap approximation — doesn't handle inline)
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for m in _CLAUDE_MODEL_RE.finditer(line):
            model = m.group(1)
            if model in CANONICAL_MODELS:
                continue
            findings.append((i, model))
    return findings


def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, int, str]] = []

    if not APP_DIR.is_dir():
        print(f"✗ app dir missing: {APP_DIR}")
        return 1 if strict else 0

    for py_path in sorted(APP_DIR.rglob("*.py")):
        rel = str(py_path.relative_to(REPO_ROOT))
        if rel in LEGACY_ALIAS_ALLOWLIST:
            continue
        for lineno, model in _scan_file(py_path):
            violations.append((py_path, lineno, model))

    if violations:
        print(f"✗ LLM model freshness — {len(violations)} stale references:")
        for path, lineno, model in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{lineno}  {model}")
        print()
        print(f"Canonical models: {sorted(CANONICAL_MODELS)}")
        print("Update the hardcoded string OR add the file to")
        print("LEGACY_ALIAS_ALLOWLIST in this audit if it's intentionally")
        print("keeping the legacy string (e.g. cost-attribution table).")
        return 1 if strict else 0

    print(f"✓ every Claude model reference matches the canonical "
          f"lineup — {len(CANONICAL_MODELS)} canonical entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
