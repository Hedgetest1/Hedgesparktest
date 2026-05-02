#!/usr/bin/env python3
"""§1.7 pre-execution protocol enforcement on non-trivial commits.

Born 2026-05-02 from the brutal-CTO 10/10 elite-tier sprint Gap 2.
The existing audit_lateral_change_evidence fires on lateral-change
keywords (add, remove, migrate, restore). Non-trivial architectural
commits that DON'T use those literal words slipped through. Brutal
CTO would catch this: "your discipline gate has a keyword loophole."

Triggers:
  1. Staged diff touches >3 files in app/services/ or app/core/
  2. Staged diff adds >50 lines net to non-test files in app/
  3. Staged diff touches any TIER_1 or TIER_2 path (per CLAUDE.md §10)

When ANY trigger fires, the commit message must include at least
ONE §1.7 evidence marker (same set as audit_lateral_change_evidence).
Missing → FAIL.

This audit is the GENERAL pre-execution protocol enforcement; the
lateral-change audit is the SPECIFIC keyword-driven case. Both
must be satisfied; both honor `# bypass` opt-out only by founder.

Usage:
    python3 scripts/audit_non_trivial_commit_protocol.py [--msg-file <path>]
    Exit 0 = clean. Exit 1 = non-trivial commit without §1.7 evidence.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO = "/opt/wishspark"
APP_PREFIX = "backend/app/"
TEST_PREFIX = "backend/tests/"

# Same evidence markers as audit_lateral_change_evidence — keep in sync.
EVIDENCE_RE = re.compile(
    r"\b(sibling[ -]hunt|sibling[ -]sweep|lateral[ -]impact|"
    r"sticky[ -]state|sticky-state read|per founder|"
    r"founder directive|founder note[d]?|founder mandate|founder asked|"
    r"founder confirmed|founder explicitly|"
    r"§\s*1\.7|section 1\.7|CLAUDE\.md\s*§\s*1\.7|"
    r"3-DA|three-DA|three lens|tre lens|"
    r"Axis 0|Pre-mortem)\b",
    re.IGNORECASE,
)

# TIER_1 / TIER_2 paths from CLAUDE.md §10. Touching ANY of these
# triggers the protocol requirement regardless of diff size.
_TIER_HIGH_PATHS = [
    # TIER_2 — never modify without explicit approval
    "backend/app/core/token_crypto.py",
    "backend/app/core/merchant_session.py",
    "backend/app/api/shopify_oauth.py",
    "backend/app/api/billing.py",
    "backend/app/core/deps.py",
    "backend/app/api/webhooks.py",
    "backend/app/services/order_ingestion.py",
    "backend/app/services/gdpr_processor.py",
    "backend/migrations/",
    "ecosystem.config.js",
    ".env",
    "deploy.sh",
    # TIER_1 — propose only, human approves
    "backend/app/services/orchestrator",
    "backend/app/services/bugfix_pipeline.py",
    "backend/app/services/promotion_pipeline.py",
    "backend/app/services/reviewer_layer.py",
    "backend/app/services/project_brain.py",
    "backend/app/core/llm_budget.py",
    "backend/app/core/llm_router.py",
    "backend/app/models/",
]

# Trivial commit subject lines — opt out
TRIVIAL_PATTERNS = re.compile(
    r"^\s*(typo|fix typo|comment fix|formatting|whitespace|"
    r"chore\(deps\)|chore\(lockfile\)|version bump|"
    r"docs:|chore:.*memory|chore:.*memo)\b",
    re.IGNORECASE,
)


def _read_msg_file(path: str | None) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        lines = [ln for ln in raw.split("\n") if not ln.lstrip().startswith("#")]
        return "\n".join(lines).strip()
    except Exception:
        return ""


def _get_commit_msg(msg_file: str | None) -> str:
    if msg_file:
        text = _read_msg_file(msg_file)
        if text:
            return text
    cem = os.path.join(REPO, ".git", "COMMIT_EDITMSG")
    text = _read_msg_file(cem)
    if text:
        return text
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=REPO, stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _staged_diff_files() -> list[str]:
    """Return paths of files in the staged diff."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"],
            cwd=REPO, stderr=subprocess.DEVNULL,
        )
        return [p for p in out.decode().splitlines() if p.strip()]
    except Exception:
        return []


def _staged_diff_added_lines() -> int:
    """Return total added line count in the staged diff for non-test
    files under app/."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--numstat"],
            cwd=REPO, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return 0
    total = 0
    for line in out.decode().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_str, _removed, path = parts
        if not added_str.isdigit():
            continue
        if not path.startswith(APP_PREFIX):
            continue
        if path.startswith(TEST_PREFIX):
            continue
        total += int(added_str)
    return total


def _classify_triggers(files: list[str], added_lines: int) -> list[str]:
    """Return human-readable list of triggers that fired."""
    triggers: list[str] = []
    # 1. >3 files in services/ or core/
    high_dir_count = sum(
        1 for p in files
        if (p.startswith("backend/app/services/") or
            p.startswith("backend/app/core/"))
    )
    if high_dir_count > 3:
        triggers.append(
            f"high-dir-count: {high_dir_count} files touched in services/+core/ (cap 3)"
        )
    # 2. >50 net added lines in non-test app/
    if added_lines > 50:
        triggers.append(
            f"diff-size: {added_lines} added lines in non-test app/ (cap 50)"
        )
    # 3. TIER_1 / TIER_2 path touch
    high_paths = [
        p for p in files
        if any(p.startswith(prefix) or p == prefix for prefix in _TIER_HIGH_PATHS)
    ]
    if high_paths:
        triggers.append(
            f"tier-high-path: {len(high_paths)} TIER_1/TIER_2 file(s) touched: "
            + ", ".join(sorted(high_paths)[:3])
            + (" ..." if len(high_paths) > 3 else "")
        )
    return triggers


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--msg-file", default=None)
    ap.add_argument("--lenient", action="store_true")
    args = ap.parse_args()

    msg = _get_commit_msg(args.msg_file)
    if not msg.strip():
        print("audit_non_trivial_commit_protocol: skip — no commit message")
        return 0

    first_line = msg.split("\n", 1)[0]
    if TRIVIAL_PATTERNS.search(first_line):
        print(
            f"audit_non_trivial_commit_protocol: skip — trivial commit "
            f"({first_line[:60]})"
        )
        return 0

    files = _staged_diff_files()
    if not files:
        # No staged diff = post-commit / ad-hoc invocation; skip
        print("audit_non_trivial_commit_protocol: skip — no staged diff")
        return 0
    added = _staged_diff_added_lines()

    triggers = _classify_triggers(files, added)
    if not triggers:
        print(
            f"audit_non_trivial_commit_protocol: OK — commit is "
            f"non-architectural ({len(files)} files, {added} added lines)"
        )
        return 0

    # Look for §1.7 evidence
    evidence_hits = EVIDENCE_RE.findall(msg)
    if evidence_hits:
        print(
            f"audit_non_trivial_commit_protocol: OK — non-trivial commit "
            f"with §1.7 evidence ({len(evidence_hits)} marker(s) found, "
            f"{len(triggers)} trigger(s))"
        )
        return 0

    print("audit_non_trivial_commit_protocol: FAIL")
    print()
    print("  This commit triggered the §1.7 pre-execution protocol "
          "requirement for one or more reasons:")
    for t in triggers:
        print(f"    - {t}")
    print()
    print("  But the message contains NO §1.7 evidence marker. Per "
          "CLAUDE.md §1.7, every non-trivial commit must include "
          "visible markers proving the 5-step checklist was followed:")
    print("    - Axis 0 risk-weight + scope")
    print("    - Sibling hunt (grep -n cited)")
    print("    - Sticky-state read (project_*.md memo cited)")
    print("    - Pre-mortem (1 paragraph)")
    print("    - 3-DA with grep evidence")
    print()
    print("  Add at least ONE marker to the commit body. Examples:")
    print("    - \"§1.7 evidence:\" header line")
    print("    - \"Sibling hunt: <grep result>\"")
    print("    - \"Pre-mortem: <paragraph>\"")
    print("    - \"3-DA: ...\"")
    print()
    print("  This audit complements audit_lateral_change_evidence by "
          "catching non-trivial commits whose subject doesn't use "
          "literal add/remove/migrate keywords but still re-shapes "
          "the codebase.")
    return 0 if args.lenient else 1


if __name__ == "__main__":
    sys.exit(main())
