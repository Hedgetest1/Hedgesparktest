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
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from _audit_io import safe_read_text

TASKS_ROOT = Path("/tmp/claude-0")
MEMORY_DIR = Path("/root/.claude/projects/-opt-wishspark/memory")
# Cross-session persistence (Phase F). One JSON file accumulates aggregate
# pattern counts across every harvester run, surviving the /tmp wipe
# between sessions. Each run merges the new window's counts into the
# rolling 30-day totals + emits a "trends" section in the memo.
CROSS_SESSION_LEDGER = MEMORY_DIR / "session_telemetry_rolling_ledger.json"
_LEDGER_RETENTION_DAYS = 30

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
        text = safe_read_text(f)
        if text is None:
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


def _load_cross_session_ledger() -> dict:
    """Load the rolling 30-day ledger (Phase F cross-session persistence).
    Schema:
      { "<YYYY-MM-DD>": {
            "total_tasks": int, "preflight_blocks": int,
            "forbidden_phrase_hits": int, "da_evidence_hits": int,
            "tier1_fallbacks": int, "shipped": int, "audit_failures": {<name>: int}
        } }
    Older keys (>30d) are pruned on each write."""
    if not CROSS_SESSION_LEDGER.is_file():
        return {}
    text = safe_read_text(CROSS_SESSION_LEDGER)
    if text is None:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def _save_cross_session_ledger(ledger: dict, today: str, current: dict) -> None:
    """Merge today's counts into the ledger, prune entries older than
    _LEDGER_RETENTION_DAYS, write back atomically."""
    merged = dict(ledger)
    merged[today] = {
        "total_tasks": current["total_tasks"],
        "preflight_blocks": current["preflight_blocks"],
        "forbidden_phrase_hits": current["forbidden_phrase_hits"],
        "da_evidence_hits": current["da_evidence_hits"],
        "tier1_fallbacks": current["tier1_manual_deploy_fallback"],
        "shipped": len(current["shipped_commits"]),
        "audit_failures": dict(current["audit_failures"]),
    }
    cutoff = datetime.now() - timedelta(days=_LEDGER_RETENTION_DAYS)
    pruned: dict = {}
    for k, v in merged.items():
        try:
            if datetime.strptime(k, "%Y-%m-%d") >= cutoff:
                pruned[k] = v
        except Exception:
            continue
    CROSS_SESSION_LEDGER.write_text(json.dumps(pruned, indent=2, sort_keys=True))


def _cross_session_trends(ledger: dict) -> list[str]:
    """Compute simple trend lines from the rolling ledger:
      - total commits shipped over window
      - top-3 audit_failures by frequency
      - days with preflight_blocks > 5 (high-friction days)"""
    if not ledger:
        return []
    days_with_data = sorted(ledger.keys())
    total_shipped = sum(int(d.get("shipped", 0)) for d in ledger.values())
    cumulative_audits: Counter = Counter()
    high_friction_days = 0
    for d in ledger.values():
        af = d.get("audit_failures") or {}
        for name, n in af.items():
            cumulative_audits[name] += int(n)
        if int(d.get("preflight_blocks", 0)) > 5:
            high_friction_days += 1
    out = [
        f"Window: {days_with_data[0]} → {days_with_data[-1]} "
        f"({len(days_with_data)} day(s) recorded)",
        f"Total shipped commits across window: **{total_shipped}**",
        f"High-friction days (preflight_blocks > 5): **{high_friction_days}**",
    ]
    if cumulative_audits:
        out.append("Top-3 audit failures (rolling):")
        for name, count in cumulative_audits.most_common(3):
            out.append(f"  - `{name}` — {count} hit(s)")
    return out


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
    # Phase F — cross-session trends from the rolling ledger
    ledger = _load_cross_session_ledger()
    trends = _cross_session_trends(ledger)
    if trends:
        lines.append("## Cross-session trends (rolling 30d ledger)")
        for t in trends:
            lines.append(f"- {t}")
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


_SCAFFOLD_DIR = Path("/opt/wishspark/backend/scripts/scaffolded_preventers")
_SCAFFOLD_THRESHOLD = 10  # rolling-window hits required to auto-scaffold


def _scaffold_preventer(audit_name: str, total_hits: int) -> Path | None:
    """Phase D — when a recurring failure pattern crosses the rolling
    threshold, write a stub preventer file the founder + agent can
    flesh out. The stub contains TODOs + the failure pattern source.
    Skip if a scaffold for the same audit already exists (idempotent).
    """
    _SCAFFOLD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_]", "_", audit_name.lower())
    out_path = _SCAFFOLD_DIR / f"scaffold_for_{safe}.py"
    if out_path.exists():
        return None  # already scaffolded — don't overwrite human work
    content = f'''#!/usr/bin/env python3
"""AUTO-SCAFFOLDED preventer stub — fill in the body.

Generated by session_telemetry_harvester.py on {datetime.now().strftime('%Y-%m-%d')}
because audit `{audit_name}` failed {total_hits} times in the rolling
30-day window. The harvester suggests this is a recurring class of
bug worth preventing earlier.

TODO: fill in the body. Pattern to follow (see existing audit_*.py
for inspiration):

  1. Identify the precise CODE PATTERN that triggers `{audit_name}` to fail.
  2. Write an AST-walk OR regex scan that catches the pattern in
     app/ source.
  3. Print FAIL + remediation hint when found, exit 1.
  4. Move this file from scaffolded_preventers/ to scripts/ when ready.
  5. Wire into preflight.sh + invariant_monitor._AUDITS.

Failure-class context:
  - `{audit_name}` failed {total_hits} times in the rolling window.
  - The harvester treats >= {_SCAFFOLD_THRESHOLD} as the threshold for
    "this needs an earlier-stage preventer".
"""
import sys


def main() -> int:
    print("FAIL: this preventer is scaffolded but not yet implemented")
    print("Edit backend/scripts/scaffolded_preventers/{out_path.name}")
    print("then move to backend/scripts/ and wire to preflight + invariant_monitor.")
    return 0  # exit 0 until implemented (don't block commits)


if __name__ == "__main__":
    sys.exit(main())
'''
    out_path.write_text(content)
    return out_path


def _maybe_scaffold_preventers(ledger: dict) -> list[Path]:
    """Walk the rolling ledger, sum audit_failures across days, scaffold
    a stub for any audit that crossed _SCAFFOLD_THRESHOLD. Returns the
    list of paths actually written this run."""
    if not ledger:
        return []
    cumulative: Counter = Counter()
    for d in ledger.values():
        af = d.get("audit_failures") or {}
        for name, n in af.items():
            cumulative[name] += int(n)
    scaffolded: list[Path] = []
    for name, total in cumulative.items():
        if total < _SCAFFOLD_THRESHOLD:
            continue
        path = _scaffold_preventer(name, total)
        if path is not None:
            scaffolded.append(path)
    return scaffolded


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
    # Phase F — persist today's counts into the rolling ledger
    try:
        ledger = _load_cross_session_ledger()
        _save_cross_session_ledger(ledger, today, summary)
    except Exception as exc:
        print(f"WARN: ledger save failed (non-fatal): {exc}", file=sys.stderr)
    # Phase D — auto-scaffold preventer stubs for recurring failure classes
    try:
        ledger_after = _load_cross_session_ledger()
        scaffolded = _maybe_scaffold_preventers(ledger_after)
        for p in scaffolded:
            print(f"  scaffolded preventer stub: {p}")
    except Exception as exc:
        print(f"WARN: scaffold pass failed (non-fatal): {exc}", file=sys.stderr)
    print(f"OK: telemetry memo written to {out_path}")
    print(f"  total_tasks={summary['total_tasks']} "
          f"preflight_blocks={summary['preflight_blocks']} "
          f"forbidden_phrase_hits={summary['forbidden_phrase_hits']} "
          f"shipped={len(summary['shipped_commits'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
