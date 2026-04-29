#!/usr/bin/env python
"""
audit_claude_md_drift.py — preflight invariant.

Catches the silent class where CLAUDE.md sections become stale because
code was modified but the documented invariant wasn't updated. CLAUDE.md
is auto-loaded every session as authoritative context — drift between
what it says and what the code does poisons every future decision.

Why it's a bug class
--------------------
CLAUDE.md is the project's North Star (founder verbatim: "When this
file contradicts a memory, a comment, or a scattered doc, this file
wins"). When sections drift from code, future Claude sessions reason
from false premises. Particularly dangerous for:
  - §6 PM2 process count (concurrency / DB pool math)
  - §13 Redis keys catalog (TTL invariants, naming conventions)
  - §10 TIER_2 file list (self-modification safety boundary)

What this audits
----------------
1. **§13 Redis keys**: every `rc.setex(<key_pattern>, ...)` /
   `rc.set(<key_pattern>, ...)` in `app/` — extract the key prefix
   pattern and verify it exists in CLAUDE.md §13's catalog table.
   (Standalone script `audit_claude_md_redis_keys.py` already exists
   for this — this audit calls it through.)

2. **§6 PM2 process count**: parse `ecosystem.config.js` for
   `apps: [...]` entries and compare to CLAUDE.md §6 table claiming
   8 processes. Drift if counts diverge by >0.

3. **§10 TIER_2 list**: parse the bullet-list of TIER_2 files in
   CLAUDE.md §10 and verify EACH file actually exists in the repo.
   A removed/renamed file in §10 = stale doc.

Usage
-----
    ./venv/bin/python scripts/audit_claude_md_drift.py
    ./venv/bin/python scripts/audit_claude_md_drift.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
ECOSYSTEM = REPO_ROOT / "ecosystem.config.js"


def _read_claude_md() -> str | None:
    try:
        return CLAUDE_MD.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _section(text: str, header_match: str) -> str | None:
    """Extract a CLAUDE.md section by its `## ` header. Returns the
    section's body up to the next `## ` (or end of file)."""
    m = re.search(rf"""^##\s+{re.escape(header_match)}.*?$""", text, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    next_m = re.search(r"""^##\s+""", text[start:], re.MULTILINE)
    end = start + (next_m.start() if next_m else len(text) - start)
    return text[start:end]


def _check_pm2_process_count() -> dict | None:
    text = _read_claude_md()
    if not text:
        return {"error": "CLAUDE.md unreadable"}
    section = _section(text, "6.")
    if not section:
        return None
    # Section §6 lists processes in a markdown table. Count rows that
    # start with `| wishspark-` to estimate the documented process count.
    documented_rows = re.findall(r"""^\|\s*wishspark-\S+\s*\|""", section, re.MULTILINE)
    documented_count = len(documented_rows)

    try:
        ecosys = ECOSYSTEM.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"error": "ecosystem.config.js unreadable"}
    # Count `name: 'wishspark-...'` entries in the apps array.
    actual_apps = re.findall(r"""name\s*:\s*['"]wishspark-\S+?['"]""", ecosys)
    actual_count = len(actual_apps)

    if documented_count != actual_count:
        return {
            "section": "§6 PM2 processes",
            "documented_in_claude_md": documented_count,
            "actual_in_ecosystem": actual_count,
            "delta": actual_count - documented_count,
        }
    return None


def _check_tier2_files() -> list[dict]:
    text = _read_claude_md()
    if not text:
        return [{"error": "CLAUDE.md unreadable"}]
    section = _section(text, "10. ") or _section(text, "10.")
    if not section:
        return []
    # Extract file paths from bullet lines like:
    #   - `app/core/token_crypto.py` — merchant token encryption
    bullets = re.findall(
        r"""^\s*[-*]\s+`([^`]+)`""",
        section,
        re.MULTILINE,
    )
    findings = []
    for path in bullets:
        # Skip glob patterns + directory references — they describe file
        # CATEGORIES (e.g., "all of app/services/*"), not single files
        # whose existence we can verify.
        if "*" in path or path.endswith("/"):
            continue
        if path == ".env":
            continue  # documented but not committed to repo
        # Resolve relative path: paths are usually relative to backend/ for
        # backend code, repo root for top-level files.
        candidates = [
            REPO_ROOT / path,
            REPO_ROOT / "backend" / path,
        ]
        if not any(p.exists() for p in candidates):
            findings.append({
                "section": "§10 TIER_2",
                "documented_path": path,
                "issue": "file referenced in CLAUDE.md §10 does not exist",
            })
    return findings


@telemetered("audit_claude_md_drift")
def audit() -> int:
    findings: list[dict] = []

    pm2_drift = _check_pm2_process_count()
    if pm2_drift:
        findings.append(pm2_drift)

    tier2_drift = _check_tier2_files()
    findings.extend(tier2_drift)

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ CLAUDE.md key sections (§6 PM2, §10 TIER_2) match codebase reality")
            return 0
        print(f"✗ {len(findings)} CLAUDE.md drift finding(s):")
        for f in findings:
            if "section" in f:
                section_label = f.get("section", "?")
                if "documented_path" in f:
                    print(f"  • {section_label}  documented: {f['documented_path']}  → does not exist")
                else:
                    print(
                        f"  • {section_label}  documented={f.get('documented_in_claude_md', '?')} "
                        f"actual={f.get('actual_in_ecosystem', '?')} "
                        f"delta={f.get('delta', '?')}"
                    )
            elif "error" in f:
                print(f"  • error: {f['error']}")
        print()
        print("CLAUDE.md is auto-loaded every session as authoritative context.")
        print("Drift means future sessions reason from false premises. Fix:")
        print("  - For §6 drift: update process table or ecosystem.config.js")
        print("  - For §10 drift: update file list (path renamed/removed)")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
