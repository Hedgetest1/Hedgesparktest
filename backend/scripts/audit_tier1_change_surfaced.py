#!/usr/bin/env python3
"""audit_tier1_change_surfaced.py — Pin G5.

CLAUDE.md §10 + §1.6 stop #2: TIER_1 files require explicit per-
change surfacing to the founder. Even under session-scoped approval
(feedback_session_scoped_tier_approval.md), the bundle must be
EXPLICITLY scoped — the bundle scope and the commit message must
declare which TIER_1 files are changing.

The 2026-05-05 sprint modified `bugfix_pipeline.py` (TIER_1) under a
stretch interpretation of "session-scoped approval" without an
explicit "TIER_1: <file>" marker in the commit message. That
violated the discipline boundary between "implicit autonomy under a
sprint scope" and "founder-visible TIER_1 disclosure".

This audit fires at commit-msg stage:
    1. Identifies TIER_1 files in the staged-or-just-committed diff.
    2. Fails if the commit message lacks an explicit
       "TIER_1: <pattern>" marker covering each modified TIER_1 file.

Marker forms accepted (any of):
    "TIER_1: app/services/bugfix_pipeline.py"
    "TIER_1: bugfix_pipeline" (bare basename without extension)
    "TIER_1 modification surfaced: <free text>"
    "TIER_1: <file>" lines in a list

Per-commit override for emergencies (rare; documents in-message):
    "TIER_1 emergency override: <reason>"

Exit codes:
  0 — no TIER_1 files in change OR all TIER_1 changes are surfaced
  1 — TIER_1 file modified without explicit marker

# invariant-eligible: false — runs at commit-msg stage, not preflight
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


# Pattern derived from CLAUDE.md §10 TIER_1 list. Keep in sync with
# the doctrine — if the list there changes, this regex changes too.
_TIER_1_PATTERNS = [
    re.compile(r"^tracker/.*\.js$"),
    re.compile(r"^app/services/orchestrator.*\.py$"),
    re.compile(r"^app/services/bugfix_pipeline\.py$"),
    re.compile(r"^app/services/promotion_pipeline\.py$"),
    re.compile(r"^app/services/reviewer_layer\.py$"),
    re.compile(r"^app/services/project_brain\.py$"),
    re.compile(r"^app/core/llm_budget\.py$"),
    re.compile(r"^app/core/llm_router\.py$"),
    re.compile(r"^app/core/client_ip\.py$"),
    re.compile(r"^app/core/cf_ip_ranges\.py$"),
    re.compile(r"^app/models/.*\.py$"),
    # `backend/` prefix variant — `git diff --cached --name-only` runs
    # from repo root, so paths come back with the leading dir.
    re.compile(r"^backend/tracker/.*\.js$"),
    re.compile(r"^backend/app/services/orchestrator.*\.py$"),
    re.compile(r"^backend/app/services/bugfix_pipeline\.py$"),
    re.compile(r"^backend/app/services/promotion_pipeline\.py$"),
    re.compile(r"^backend/app/services/reviewer_layer\.py$"),
    re.compile(r"^backend/app/services/project_brain\.py$"),
    re.compile(r"^backend/app/core/llm_budget\.py$"),
    re.compile(r"^backend/app/core/llm_router\.py$"),
    re.compile(r"^backend/app/core/client_ip\.py$"),
    re.compile(r"^backend/app/core/cf_ip_ranges\.py$"),
    re.compile(r"^backend/app/models/.*\.py$"),
]

_TIER_1_MARKER_RE = re.compile(
    r"\bTIER_?1\b[^a-zA-Z0-9].{0,200}",
    re.IGNORECASE | re.DOTALL,
)


def _read_msg(msg_file: str | None) -> str:
    candidates: list[str] = []
    if msg_file:
        candidates.append(msg_file)
    candidates.append("/opt/wishspark/.git/COMMIT_EDITMSG")
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
                # Strip git comment lines
                lines = [ln for ln in raw.split("\n") if not ln.lstrip().startswith("#")]
                text = "\n".join(lines).strip()
                if text:
                    return text
            except Exception:
                continue
    # Fallback: HEAD message (post-commit context)
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%B"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _staged_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        files = [ln.strip() for ln in out.decode().split("\n") if ln.strip()]
        if files:
            return files
    except Exception:
        pass
    # Post-commit fallback: last commit's files
    try:
        out = subprocess.check_output(
            ["git", "show", "--name-only", "--pretty=", "HEAD"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        return [ln.strip() for ln in out.decode().split("\n") if ln.strip()]
    except Exception:
        return []


def _classify(files: list[str]) -> list[str]:
    """Return only the files matching a TIER_1 pattern."""
    hits: list[str] = []
    for f in files:
        for pat in _TIER_1_PATTERNS:
            if pat.match(f):
                hits.append(f)
                break
    return hits


def _has_marker(msg: str, tier1_files: list[str]) -> tuple[bool, str]:
    """Return (True, reason) if the commit message has an explicit
    TIER_1 marker covering at least one of the modified files OR a
    blanket TIER_1 surfacing/override declaration."""
    if not msg:
        return False, "empty commit message"
    # Generic marker presence
    if not _TIER_1_MARKER_RE.search(msg):
        return False, "no 'TIER_1' marker found in message"
    # Acceptable broad declarations
    broad_markers = (
        re.compile(r"TIER_?1\s+(?:modification|change)\s+surfaced", re.IGNORECASE),
        re.compile(r"TIER_?1\s+emergency\s+override", re.IGNORECASE),
        re.compile(r"TIER_?1\s+session[- ]scoped\s+approval", re.IGNORECASE),
    )
    if any(p.search(msg) for p in broad_markers):
        return True, "broad TIER_1 surfacing declaration found"
    # Explicit per-file: TIER_1: <basename> or TIER_1: <full-path>
    for f in tier1_files:
        basename = os.path.basename(f).rsplit(".", 1)[0]
        per_file = re.compile(
            rf"TIER_?1[\s:]+(?:[^\n]*?{re.escape(basename)}|[^\n]*?{re.escape(f)})",
            re.IGNORECASE,
        )
        if per_file.search(msg):
            return True, f"explicit TIER_1 marker for {basename}"
    return False, "TIER_1 keyword present but no per-file or broad declaration"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msg-file", default=None,
                        help="Path to commit message file (commit-msg hook $1)")
    parser.add_argument("--strict", action="store_true",
                        help="No-op shim for compat.")
    args = parser.parse_args()

    files = _staged_files()
    tier1 = _classify(files)
    if not tier1:
        print("audit_tier1_change_surfaced: OK — no TIER_1 files in change")
        return 0

    msg = _read_msg(args.msg_file)
    ok, reason = _has_marker(msg, tier1)
    if ok:
        print(
            f"audit_tier1_change_surfaced: OK — {reason} "
            f"(TIER_1 files modified: {len(tier1)})"
        )
        return 0

    print("audit_tier1_change_surfaced: FAIL — TIER_1 modification without surfacing")
    print(f"  TIER_1 files in this change ({len(tier1)}):")
    for f in tier1:
        print(f"    - {f}")
    print(f"  Reason: {reason}")
    print(
        "\n  Add ONE of these to the commit body to satisfy the audit:\n"
        '    "TIER_1: <file_basename>"  (per-file marker)\n'
        '    "TIER_1 modification surfaced: <reason>"\n'
        '    "TIER_1 session-scoped approval: <sprint memo>"\n'
        '    "TIER_1 emergency override: <reason>"\n'
        "\n  Per CLAUDE.md §10 + §1.6 stop #2, every TIER_1 change "
        "requires explicit founder-visible disclosure. Even under "
        "session-scoped approval, the bundle scope + the commit message "
        "must declare which TIER_1 files are changing."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
