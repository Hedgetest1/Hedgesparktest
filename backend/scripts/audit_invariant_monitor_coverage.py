#!/usr/bin/env python3
"""Invariant-monitor coverage preventer.

Catches the bug class where a NEW preflight audit ships state-based
(scans the codebase) but is NOT wired into invariant_monitor._AUDITS,
so post-merge drift escapes the periodic 15-min cycle. Brutal CTO:
"your preventers catch new commits but go quiet on existing drift."

Born 2026-05-02 from the brutal-CTO sprint after the auth_hardening
multidim sweep surfaced 60 preflight audits orphan to invariant_monitor.
Some are LEGITIMATELY commit-stage-only (read git diff, COMMIT_EDITMSG,
$1 arg). Others are state-based and SHOULD run periodically. This audit
auto-classifies + reports the orphans that should be wired.

Heuristic — an audit is COMMIT-STAGE-ONLY (skip) if its source contains
ANY of these patterns:
  - reads `--text-file`, `--msg-file`, `--commit`, `sys.argv[1]`
  - reads `git diff --cached` / `git diff --staged`
  - reads `COMMIT_EDITMSG` / `COMMIT_MSG_FILE`
  - reads `git rev-parse HEAD` AS the primary input

Otherwise it's STATE-BASED (eligible for invariant_monitor).

For each STATE-BASED audit not in invariant_monitor._AUDITS, this audit
emits a finding: either wire it OR add `# invariant-eligible: false` to
the script header to mark it as intentional opt-out.

Currently INFO-ONLY (exit 0 even with orphans) until the eligible set
is fully classified; the founder can flip --strict on once the bulk
triage is done.

Usage:
    python3 scripts/audit_invariant_monitor_coverage.py
    python3 scripts/audit_invariant_monitor_coverage.py --strict
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO / "scripts"
PREFLIGHT_SH = REPO / "scripts" / "preflight.sh"
INVARIANT_MONITOR = REPO / "app" / "services" / "invariant_monitor.py"

# Patterns that mark an audit as commit-stage-only (read git/commit
# message context). If any matches, the audit is NOT eligible for
# periodic scan — it depends on per-commit input that doesn't exist
# at the periodic cycle.
_COMMIT_STAGE_PATTERNS = [
    r"--text-file\b",
    r"--msg-file\b",
    r"--commit\b",
    r"\bsys\.argv\[\s*1\s*\]",
    r"git\s+diff\s+--cached",
    r"git\s+diff\s+--staged",
    r"COMMIT_EDITMSG\b",
    r"COMMIT_MSG_FILE\b",
    r"git\s+rev-parse\s+HEAD",
]
_COMMIT_STAGE_RE = re.compile("|".join(_COMMIT_STAGE_PATTERNS))

# Explicit opt-out tag in the audit script header (optional). Honored
# verbatim — author's claim that the audit is intentionally commit-
# stage-only or otherwise not meant for periodic scan. Trailing
# parenthetical reason after `false` is allowed and recommended,
# e.g. `# invariant-eligible: false  (has runtime HTTP side-effects)`.
_OPT_OUT_TAG = re.compile(
    r"^\s*#\s*invariant-eligible\s*:\s*false\b",
    re.MULTILINE,
)
# Author can also explicitly opt IN, useful when the heuristic
# would otherwise mis-classify a state-based audit as commit-stage.
_OPT_IN_TAG = re.compile(
    r"^\s*#\s*invariant-eligible\s*:\s*true\b",
    re.MULTILINE,
)


def parse_invariant_monitor_audits() -> set[str]:
    """Extract the set of audit-script basenames currently wired into
    invariant_monitor._AUDITS via AST parse."""
    if not INVARIANT_MONITOR.is_file():
        return set()
    try:
        tree = ast.parse(INVARIANT_MONITOR.read_text())
    except SyntaxError:
        return set()
    wired: set[str] = set()
    for node in ast.walk(tree):
        # Look for the _AUDITS list assignment (Assign or AnnAssign)
        targets: list = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            if node.target is not None:
                targets = [node.target]
            value = node.value
        if not targets or value is None:
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "_AUDITS" for t in targets
        ):
            continue
        if not isinstance(value, ast.List):
            continue
        for tup in value.elts:
            if isinstance(tup, ast.Tuple) and tup.elts:
                first = tup.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    wired.add(first.value)
    return wired


def parse_preflight_audits() -> list[str]:
    """Extract the set of audit-script basenames invoked from
    preflight.sh."""
    if not PREFLIGHT_SH.is_file():
        return []
    text = PREFLIGHT_SH.read_text()
    # Match `scripts/audit_<name>.py` references. De-dup preserving
    # discovery order.
    seen: set[str] = set()
    out: list[str] = []
    for m in re.findall(r"audit_[a-z_0-9]+\.py", text):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def classify_audit(script_path: Path) -> str:
    """Return one of 'commit-stage', 'state-based', or 'opt-out'."""
    text = safe_read_text(script_path)
    if text is None:
        return "state-based"  # default eligible if unreadable
    if _OPT_OUT_TAG.search(text):
        return "opt-out"
    if _OPT_IN_TAG.search(text):
        return "state-based"
    if _COMMIT_STAGE_RE.search(text):
        return "commit-stage"
    return "state-based"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on any unclassified state-based orphan")
    args = ap.parse_args()

    wired = parse_invariant_monitor_audits()
    preflight = parse_preflight_audits()
    if not preflight:
        print("FAIL: no audit references found in preflight.sh")
        return 1

    orphans_state_based: list[str] = []
    skip_commit_stage: list[str] = []
    skip_opt_out: list[str] = []

    for name in preflight:
        if name in wired:
            continue
        path = SCRIPTS_DIR / name
        if not path.is_file():
            continue  # file missing — separate gap, not our concern
        kind = classify_audit(path)
        if kind == "commit-stage":
            skip_commit_stage.append(name)
        elif kind == "opt-out":
            skip_opt_out.append(name)
        else:  # state-based
            orphans_state_based.append(name)

    print(
        f"invariant_monitor coverage: {len(wired)} wired, "
        f"{len(preflight)} preflight audits scanned"
    )
    print(
        f"  ✓ commit-stage-only (auto-skip): {len(skip_commit_stage)}"
    )
    print(
        f"  ✓ opt-out tagged (intentional skip): {len(skip_opt_out)}"
    )
    print(
        f"  ⚠ state-based orphan (should be wired): "
        f"{len(orphans_state_based)}"
    )

    if orphans_state_based:
        print("\nState-based orphans (heuristic-classified — wire OR tag opt-out):")
        for name in sorted(orphans_state_based):
            print(f"  - {name}")
        print(
            "\nFix: wire each into app/services/invariant_monitor.py::_AUDITS\n"
            "OR add `# invariant-eligible: false` to the script header\n"
            "if the audit is intentionally commit-stage-only despite\n"
            "the heuristic missing the signal."
        )
        if args.strict:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
