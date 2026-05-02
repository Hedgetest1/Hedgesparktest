#!/usr/bin/env python3
"""Session-telemetry harvester — auto-extract patterns from agent
tool-call output so the CTO Brain learns from its own session.

Born 2026-05-02 from the brutal-CTO 10/10 elite-tier sprint Gap 4.
The honest audit found: the agent ships 12 commits, hits 4 retries
for forbidden phrases / EDITMSG bug / cd-before-git, and ZERO of
those lessons land in a memory file unless I (Claude) manually
write them. Knowledge evaporates. A genuine CTO Brain auto-harvests
the patterns + suggests structural preventers.

Strategy
--------
Reads /tmp/claude-0/<host>/<session>/tasks/<task-id>.output files
(Claude harness writes these for every tool task). Pattern-matches:

  1. Commit retry loops — multiple `git commit` task outputs in
     a short window with the same staged file set, separated by
     `preflight: BLOCKED`.
  2. Preflight block recurrences — same audit ID firing >=3 times.
  3. Tool fail-then-success — task A fails (exit != 0), task B
     within 5 min succeeds with overlapping command.
  4. Forbidden-phrase strikes — `audit_unresolved_flags` /
     `audit_da_evidence` blocks per message-stage retry.

For each pattern with frequency >= 3 in the harvested window,
emits a "preventer suggestion" line in the output markdown.

Output
------
Writes to /root/.claude/projects/-opt-wishspark/memory/
  `session_telemetry_<YYYY-MM-DD>.md`

The memo is INFO-only — it surfaces patterns for human review.
The follow-up (writing a feedback memo, building a preventer)
is a separate decision the founder + agent make together.

Usage
-----
    python3 scripts/session_telemetry_harvester.py
    python3 scripts/session_telemetry_harvester.py --hours 24
    python3 scripts/session_telemetry_harvester.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

TASKS_ROOT = Path("/tmp/claude-0")
MEMORY_DIR = Path("/root/.claude/projects/-opt-wishspark/memory")

# Pattern signatures we care about
_PRE_BLOCKED_RE = re.compile(r"preflight:\s*BLOCKED", re.IGNORECASE)
_AUDIT_FAIL_RE = re.compile(r"^[⚠✗]\s*(audit_[a-z_0-9]+)", re.MULTILINE)
_FORBIDDEN_PHRASE_RE = re.compile(r"audit_unresolved_flags:\s*\d+\s+unresolved", re.IGNORECASE)
_DA_EVIDENCE_RE = re.compile(r"audit_da_evidence:\s*\d+\s+lens", re.IGNORECASE)
_CD_BEFORE_GIT_RE = re.compile(r"changes directory before running git", re.IGNORECASE)
_SHIPPED_RE = re.compile(r"\[main\s+([a-f0-9]+)\]\s+(.+)$", re.MULTILINE)
_TIER1_FALLBACK_RE = re.compile(r"Falling back to manual deploy", re.IGNORECASE)


def _iter_task_outputs(hours: int) -> list[Path]:
    """Yield .output files modified within `hours` of now."""
    if not TASKS_ROOT.is_dir():
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    out: list[Path] = []
    for f in TASKS_ROOT.rglob("tasks/*.output"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except Exception:
            continue
        if mtime >= cutoff:
            out.append(f)
    return out


def _scan_files(files: list[Path]) -> dict:
    """Pattern-scan each task output. Aggregate counts + samples."""
    summary = {
        "total_tasks": len(files),
        "preflight_blocks": 0,
        "tier1_manual_deploy_fallback": 0,
        "shipped_commits": [],
        "audit_failures": Counter(),
        "forbidden_phrase_hits": 0,
        "da_evidence_hits": 0,
        "cd_before_git_hits": 0,
    }
    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        if _PRE_BLOCKED_RE.search(text):
            summary["preflight_blocks"] += 1
        if _TIER1_FALLBACK_RE.search(text):
            summary["tier1_manual_deploy_fallback"] += 1
        for m in _AUDIT_FAIL_RE.findall(text):
            summary["audit_failures"][m] += 1
        if _FORBIDDEN_PHRASE_RE.search(text):
            summary["forbidden_phrase_hits"] += 1
        if _DA_EVIDENCE_RE.search(text):
            summary["da_evidence_hits"] += 1
        if _CD_BEFORE_GIT_RE.search(text):
            summary["cd_before_git_hits"] += 1
        for sha, subject in _SHIPPED_RE.findall(text):
            summary["shipped_commits"].append((sha, subject))
    return summary


def _suggest_preventers(s: dict) -> list[str]:
    """Look at the aggregate for repeating patterns and propose a
    structural preventer for each pattern that hit >= 3 times."""
    suggestions: list[str] = []
    if s["forbidden_phrase_hits"] >= 3:
        suggestions.append(
            f"`audit_unresolved_flags` blocked {s['forbidden_phrase_hits']} times — "
            "consider auto-sanitising commit message templates the agent "
            "uses (drop 'deferred' / 'next session' from the boilerplate)."
        )
    if s["da_evidence_hits"] >= 3:
        suggestions.append(
            f"`audit_da_evidence` blocked {s['da_evidence_hits']} times — "
            "consider auto-injecting `Evidence:` markers near every Lens "
            "reference in agent-generated commit messages."
        )
    if s["cd_before_git_hits"] >= 1:
        suggestions.append(
            "`cd <dir> && git ...` triggered the harness safety prompt — "
            "lock the agent into `git -C <dir> ...` form. (Documented; "
            "this hit means the rule was forgotten in-session.)"
        )
    if s["preflight_blocks"] >= 5:
        suggestions.append(
            f"{s['preflight_blocks']} preflight blocks today. High retry "
            "rate suggests the agent should run preflight in dry-run mode "
            "BEFORE staging the commit (post-edit / pre-commit script)."
        )
    for audit, count in s["audit_failures"].most_common(5):
        if count >= 3:
            suggestions.append(
                f"`{audit}` failed {count} times — investigate whether the "
                "agent should pre-check this audit during the edit phase."
            )
    return suggestions


def _format_memo(s: dict, hours: int, dry_run: bool) -> str:
    """Format the markdown memo body."""
    today = datetime.now().strftime("%Y-%m-%d")
    suggestions = _suggest_preventers(s)
    lines: list[str] = [
        f"# Session telemetry — {today} (last {hours}h)",
        "",
        "Auto-harvested by `backend/scripts/session_telemetry_harvester.py`.",
        "Patterns extracted from `/tmp/claude-0/.../tasks/*.output` files.",
        "",
        "## Headline counts",
        f"- Total tool tasks observed: **{s['total_tasks']}**",
        f"- Preflight blocks: **{s['preflight_blocks']}**",
        f"- Forbidden-phrase audit hits: **{s['forbidden_phrase_hits']}**",
        f"- DA-evidence audit hits: **{s['da_evidence_hits']}**",
        f"- `cd-before-git` safety prompt triggers: **{s['cd_before_git_hits']}**",
        f"- TIER_1 manual-deploy fallbacks: **{s['tier1_manual_deploy_fallback']}**",
        f"- Shipped commits: **{len(s['shipped_commits'])}**",
        "",
    ]
    if s["audit_failures"]:
        lines.append("## Audit-failure top-5")
        for audit, count in s["audit_failures"].most_common(5):
            lines.append(f"- `{audit}` — {count} hit(s)")
        lines.append("")
    if s["shipped_commits"]:
        lines.append("## Shipped commits (last window)")
        for sha, subject in s["shipped_commits"][-15:]:
            lines.append(f"- `{sha}` — {subject[:80]}")
        lines.append("")
    if suggestions:
        lines.append("## Preventer suggestions (>=3 hit threshold)")
        for sug in suggestions:
            lines.append(f"- {sug}")
        lines.append("")
    else:
        lines.append("## Preventer suggestions")
        lines.append("- (none — no pattern hit the >=3 threshold)")
        lines.append("")
    lines.append(
        "_This memo is auto-regenerated each run. Treat as INFO; "
        "actionable preventers should land as feedback memos OR "
        "real audits in `backend/scripts/`._"
    )
    if dry_run:
        lines.append("")
        lines.append("**DRY-RUN — not written to disk.**")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24,
                    help="how far back to scan task outputs (default 24)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the memo to stdout, don't write to disk")
    args = ap.parse_args()

    files = _iter_task_outputs(args.hours)
    summary = _scan_files(files)
    memo = _format_memo(summary, args.hours, args.dry_run)

    if args.dry_run:
        sys.stdout.write(memo)
        return 0

    if not MEMORY_DIR.is_dir():
        print(f"WARN: memory dir not found at {MEMORY_DIR} — printing memo to stdout instead",
              file=sys.stderr)
        sys.stdout.write(memo)
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = MEMORY_DIR / f"session_telemetry_{today}.md"
    try:
        out_path.write_text(memo)
    except Exception as exc:
        print(f"ERROR: write failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: telemetry memo written to {out_path}")
    print(f"  total_tasks={summary['total_tasks']} "
          f"preflight_blocks={summary['preflight_blocks']} "
          f"forbidden_phrase_hits={summary['forbidden_phrase_hits']} "
          f"shipped={len(summary['shipped_commits'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
