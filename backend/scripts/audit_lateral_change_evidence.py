#!/usr/bin/env python3
"""Lateral-change evidence preventer.

Born 2026-04-30 from `feedback_2026_04_30_failure_mode_diagnosis.md`
+ CLAUDE.md §1.7. Pattern that broke: I executed founder commands
of the form "remove X" / "add Y" reactively, without sibling hunt or
sticky-state read, breaking visual triads and prior decisions across
8 oscillations of the Pro/Lite tier partition in one session.

This audit blocks commits whose subject line OR body contains
lateral-change keywords (`remove` / `rimuovi` / `add` / `aggiungi` /
`migrate` / `sposta` / `restore` / `ripristina`) UNLESS the body
ALSO contains evidence that the §1.7 pre-execution protocol was
followed:

  - "Sibling hunt:" line (or "sibling sweep")
  - "Lateral impact:" line
  - "Sticky-state:" line (or "sticky state read")
  - "Per founder:" / "Per founder directive" (= founder explicitly
    requested with awareness of the lateral implications, audit
    bypassed)

If the keyword is present but no evidence, the commit is BLOCKED
with a message pointing to CLAUDE.md §1.7.

Bypass: `--lenient` flag downgrades to warning (not used in
preflight by default).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

LATERAL_KEYWORDS = re.compile(
    r"\b(remove[ds]?|removing|rimuov[aoie]+|add(ed|ing)?|aggiung[aoie]+|"
    r"migrate[ds]?|migrating|sposta[a-z]*|restore[ds]?|restoring|"
    r"ripristin[aoie]+|disable[ds]?|disabling|enable[ds]?|enabling|"
    r"strip[s]?|stripping|consolidat[a-z]+)\b",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(
    r"\b(sibling[ -]hunt|sibling[ -]sweep|lateral[ -]impact|"
    r"sticky[ -]state|sticky-state read|per founder|"
    r"founder directive|founder note[d]?|founder mandate|founder asked|"
    r"founder confirmed|founder explicitly|"
    r"§\s*1\.7|section 1\.7|CLAUDE\.md\s*§\s*1\.7|"
    r"3-DA|three-DA|three lens|tre lens)\b",
    re.IGNORECASE,
)

# Trivial-commit signal — these don't need lateral-change evidence.
TRIVIAL_PATTERNS = re.compile(
    r"^\s*(typo|fix typo|comment fix|formatting|whitespace|"
    r"chore\(deps\)|chore\(lockfile\)|version bump|"
    r"docs:|chore:.*memory|chore:.*memo)\b",
    re.IGNORECASE,
)


def get_commit_text() -> str:
    """Get the commit message being prepared (current HEAD or staged)."""
    # Prefer HEAD message if a commit was just made
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%B"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace")
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lenient", action="store_true",
                    help="Warn instead of block (not used in preflight)")
    args = ap.parse_args()

    text = get_commit_text()
    if not text.strip():
        print("audit_lateral_change_evidence: skip — no commit message yet")
        return 0

    # Skip trivial commits
    first_line = text.split("\n", 1)[0]
    if TRIVIAL_PATTERNS.search(first_line):
        print(f"audit_lateral_change_evidence: skip — trivial commit ({first_line[:60]})")
        return 0

    # Look for lateral keywords
    lateral_hits = LATERAL_KEYWORDS.findall(text)
    if not lateral_hits:
        print("audit_lateral_change_evidence: OK — no lateral-change keywords")
        return 0

    # Look for evidence
    evidence_hits = EVIDENCE_RE.findall(text)
    if evidence_hits:
        print(
            f"audit_lateral_change_evidence: OK — lateral-change with "
            f"§1.7 evidence ({len(evidence_hits)} marker(s) found)"
        )
        return 0

    # Lateral keyword without evidence → BLOCK
    print("audit_lateral_change_evidence: FAIL")
    print()
    print(
        "  This commit message contains lateral-change keyword(s) — "
        f"{sorted(set(lateral_hits))[:5]} — but NO §1.7 evidence."
    )
    print()
    print(
        "  Per CLAUDE.md §1.7 (the pre-execution protocol), every "
        "non-trivial change with lateral implications must include "
        "explicit evidence that the 5-step checklist was followed:"
    )
    print()
    print("    - Axis 0 risk-weight + scope")
    print("    - Sibling hunt (grep -n cited)")
    print("    - Sticky-state read (project_*.md memo cited)")
    print("    - Pre-mortem (1 paragraph)")
    print("    - 3-DA with grep evidence")
    print()
    print(
        "  Add ONE of these markers to the commit body to satisfy "
        "the audit:"
    )
    print(
        '    - "Sibling hunt: <results>" / "Sibling sweep: <findings>"'
    )
    print('    - "Lateral impact: <analysis>"')
    print('    - "Sticky-state: <memo cited>"')
    print('    - "Per founder directive 2026-MM-DD: <quote>"')
    print('    - "3-DA: <Internal/Investor/Competitor verdicts>"')
    print()
    print(
        "  This is NOT performative — it forces the lateral analysis "
        "BEFORE the change ships. The pattern this audit prevents is "
        "the 8-oscillation Pro/Lite tier session of 2026-04-30."
    )

    return 0 if args.lenient else 1


if __name__ == "__main__":
    sys.exit(main())
