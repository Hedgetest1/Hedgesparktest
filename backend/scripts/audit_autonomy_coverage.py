#!/usr/bin/env python3
"""audit_autonomy_coverage.py — meta-audit of self-fix coverage.

Born 2026-04-26 after founder directive: "Per il resto il sistema
deve essere autonomo. Rendilo tale." This audit answers the question
"which preflight audits can self-fix vs require human intervention?"
so we can systematically extend `--fix` coverage and surface the
remaining manual territories.

What it scans
=============
Every `audit_*.py` in `backend/scripts/` AND every audit wired into
`preflight.sh`. For each:

  - `wired_in_preflight`: does preflight.sh invoke it?
  - `has_fix_mode`: does the script accept `--fix` argument?
  - `class_signature`: best-guess bug class category from header
    (deterministic source-shape, semantic text, security, refactor,
    runtime-state, etc.)

Output
======
Prints a 3-column report sorted by autonomy state:

  AUTONOMOUS  — wired in preflight + has --fix (commit-time self-heal)
  DETECT-ONLY — wired in preflight, no --fix (blocks commit, needs human)
  ORPHAN      — exists but not in preflight (drift candidate)

Goal: minimize DETECT-ONLY by adding `--fix` where deterministic,
keep semantic/security cases on the human/LLM side intentionally.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path("/opt/wishspark/backend")
SCRIPTS_DIR = BACKEND / "scripts"
PREFLIGHT_SH = SCRIPTS_DIR / "preflight.sh"

# Classification heuristics from script docstring
CATEGORY_KEYWORDS = {
    "deterministic-source-shape": ["ghost", "drift", "missing entry", "parity", "freshness", "unused", "orphan"],
    "semantic-text-rewrite":      ["aria-label", "phrasing", "voice", "copy", "narrative", "currency in"],
    "security":                   ["sentry", "token", "secret", "auth", "csrf", "session", "pii", "gdpr"],
    "type-or-schema":             ["openapi", "alembic", "schema", "model", "type", "response_model"],
    "exception-handling":         ["exception", "silent", "except"],
    "config-state":               ["map sync", "redis", "pm2", "scheduled"],
    "runtime-or-deploy":          ["live", "served", "build", "bundle", "ssr"],
}

def classify_audit(path: Path) -> str:
    try:
        head = path.read_text()[:2000].lower()
    except OSError:
        return "unknown"
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in head:
                return cat
    return "uncategorized"


def has_fix_mode(path: Path) -> bool:
    # The meta-audit itself describes `--fix` in its header but doesn't
    # need its own --fix mode (it's a read-only reporter).
    if path.name == "audit_autonomy_coverage.py":
        return False
    try:
        text = path.read_text()
    except OSError:
        return False
    # Strict heuristics: argparse add_argument("--fix") OR explicit
    # `"--fix" in argv` check. Mere docstring mentions don't count.
    if re.search(r'add_argument\(\s*["\']--fix["\']', text):
        return True
    if '"--fix" in argv' in text or "'--fix' in argv" in text:
        return True
    return False


def wired_in_preflight() -> dict[str, bool]:
    if not PREFLIGHT_SH.exists():
        return {}
    text = PREFLIGHT_SH.read_text()
    wired: dict[str, bool] = {}
    for path in sorted(SCRIPTS_DIR.glob("audit_*.py")):
        name = path.name
        wired[name] = name in text
    return wired


def has_fix_in_preflight(audit_name: str) -> bool:
    """Check if preflight invokes the audit with --fix-supported flag."""
    if not PREFLIGHT_SH.exists():
        return False
    text = PREFLIGHT_SH.read_text()
    pattern = re.compile(rf'run_with_autofix\s+"[^"]*"\s+"{re.escape(audit_name)}"\s+--fix-supported')
    return bool(pattern.search(text))


def main() -> int:
    audits = sorted(SCRIPTS_DIR.glob("audit_*.py"))
    if not audits:
        print("audit_autonomy_coverage: no audits found")
        return 0

    wired = wired_in_preflight()
    rows = []
    for path in audits:
        name = path.name
        rows.append({
            "name": name,
            "wired": wired.get(name, False),
            "has_fix": has_fix_mode(path),
            "wired_with_fix": has_fix_in_preflight(name),
            "category": classify_audit(path),
        })

    autonomous = [r for r in rows if r["wired"] and r["wired_with_fix"]]
    detect_only = [r for r in rows if r["wired"] and not r["wired_with_fix"]]
    has_fix_unwired = [r for r in rows if r["has_fix"] and not r["wired_with_fix"]]
    orphan = [r for r in rows if not r["wired"]]

    print("═══ Autonomy coverage report ═══")
    print(f"Total audits: {len(rows)}")
    print(f"  AUTONOMOUS (wired + --fix-supported in preflight): {len(autonomous)}")
    print(f"  DETECT-ONLY (wired, no --fix in preflight):        {len(detect_only)}")
    print(f"  HAS --fix BUT NOT WIRED WITH IT:                   {len(has_fix_unwired)}")
    print(f"  ORPHAN (not in preflight):                         {len(orphan)}")
    print()

    print("─── AUTONOMOUS ───")
    for r in autonomous:
        print(f"  ✓ {r['name']}  [{r['category']}]")
    print()

    print("─── DETECT-ONLY (manual / LLM territory) ───")
    by_category: dict[str, list[dict]] = {}
    for r in detect_only:
        by_category.setdefault(r["category"], []).append(r)
    for cat in sorted(by_category):
        print(f"  [{cat}]")
        for r in by_category[cat]:
            mark = " (has --fix not wired)" if r["has_fix"] else ""
            print(f"    · {r['name']}{mark}")
    print()

    if has_fix_unwired:
        print("─── HAS --fix BUT PREFLIGHT NOT INVOKING IT ───")
        for r in has_fix_unwired:
            print(f"  ! {r['name']}  [{r['category']}]")
            print(f"    → wire via: run_with_autofix \"<name>\" \"{r['name']}\" --fix-supported")
        print()

    if orphan:
        print("─── ORPHAN (script exists but preflight doesn't run it) ───")
        for r in orphan[:15]:
            print(f"  ? {r['name']}")
        if len(orphan) > 15:
            print(f"  ...and {len(orphan) - 15} more")
        print()

    autonomy_pct = (100.0 * len(autonomous) / max(len(rows), 1))
    print(f"Autonomy: {len(autonomous)}/{len(rows)} ({autonomy_pct:.1f}%) audits self-heal at commit time.")
    print(f"Manual gap: {len(detect_only)} detect-only audits — see breakdown above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
