#!/usr/bin/env python3
"""
audit_unresolved_flags — Mechanical enforcement of CLAUDE.md §20.

Born 2026-04-25 after a 9.775/10 score claim was followed minutes
later by the founder catching two latent theater bugs the prior
turn's anemic Devil's-Advocate had missed. The pattern of "ship →
claim near-10 → emergency fix" is structural failure. This audit
makes the pattern mechanically forbidden by scanning the most-
recent commit message and diff for phrases that mark unresolved
flags ("Cat-A logged", "follow-up sprint", "minor improvement",
"deferred", etc.).

When such a phrase appears WITHOUT an explicit R-blocker label
from §20.1 (founder-domain / TIER_2-approval / external-dep /
sprint>1d-with-memo), the audit refuses with exit 1. The
preflight runner blocks the commit until either:

  (a) the flag is fixed in-turn (R-fix) — drop the phrase from
      the commit message
  (b) the concern is disproven with evidence (R-disprove) — drop
      the phrase from the commit message
  (c) the phrase is paired with an explicit R-blocker label naming
      the specific blocker class from §20.1

Usage
-----
    cd /opt/wishspark/backend
    ./venv/bin/python scripts/audit_unresolved_flags.py
        [--commit <sha>]                 # default: HEAD
        [--text "<text to scan>"]        # scan arbitrary text
        [--strict]                       # exit 1 on any unresolved flag (default)
        [--lenient]                      # exit 0; print findings only

Wired into preflight.sh — `git commit` BLOCKS when a forbidden
phrase ships in the commit message without an R-blocker label.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Phrases that, at turn-close, mark a flag as unresolved unless
# accompanied by an explicit R-blocker label. The regex is anchored
# to word boundaries so substring matches don't trigger spurious
# hits (e.g. "logged" inside "logged in").
FORBIDDEN_PHRASES: list[str] = [
    r"Cat-A\s+log(?:ged|s)",
    r"Cat-A\s+follow[- ]?up",
    r"Cat-B\s+log(?:ged|s)",
    r"follow[- ]?up\s+sprint",
    r"minor\s+improvement",
    r"minor\s+follow[- ]?up",
    r"next\s+session",
    r"future\s+enhancement",
    r"logged\s+for\s+later",
    r"\bTODO\b",
    r"\bfor\s+v2\b",
    r"later\s+sprint",
    r"\bdeferred\b",
    r"loggable",
    r"non[- ]blocker",
    r"small\s+polish\s+later",
    r"we\s+can\s+revisit",
    r"soon[-]?ish",
    r"ship\s+later",
    r"address\s+later",
    r"come\s+back\s+to",
    r"out\s+of\s+scope\s+for\s+now",
    # § 19.1 bug-fix reproduction law (born 2026-04-26). These phrases
    # at fix-close indicate verification was performed on cold-start /
    # empty / type-check level instead of reproducing the bug-trigger
    # conditions. Compiling ≠ fix verified. Build green ≠ runtime fixed.
    r"refresh\s+(?:and|to)\s+(?:tell|let)\s+me",
    r"refresh\s+to\s+see",
    r"hard[- ]refresh",
    r"reload\s+to\s+see",
    r"should\s+work\s+now",
    r"build\s+green\s+so\s+(?:the\s+)?runtime",
    r"preflight\s+clean\s*=\s*bug\s+fixed",
    r"type[- ]?check\s+passed\s*=\s*layout",
    r"structure\s+looks\s+correct",
    r"verified\s+(?:via|with)\s+headless\s+without\s+data",
    r"tested\s+on\s+cold[- ]start",
]

# R-blocker labels that legitimize a deferred flag per §20.1. When
# any phrase from FORBIDDEN_PHRASES is matched, we look for an
# adjacent R-blocker label within ±200 characters in the same text.
# Labels MUST be explicit; loose words like "later" or "next time"
# do NOT qualify.
R_BLOCKER_LABELS: list[str] = [
    r"\(R-blocker:\s*founder-domain\)",
    r"\(R-blocker:\s*tier[_-]?2[- ]approval\)",
    r"\(R-blocker:\s*external-dep[a-z\-]*\)",
    r"\(R-blocker:\s*sprint>1d[a-z\-]*\)",
    r"\(R-fix\)",
    r"\(R-disprove\)",
]

# Some commit messages legitimately reference past flags, e.g.
# "closes Cat-A from commit X". Such retrospective references are
# not new flags — they describe work already done. We exempt
# phrases that follow specific markers.
RETROSPECTIVE_MARKERS: list[str] = [
    r"closes\s+Cat-A",
    r"closing\s+Cat-A",
    r"Cat-A\s+closure",
    r"closes\s+the\s+Cat-A",
    r"closing\s+the\s+Cat-A",
    r"closed\s+Cat-A",
    r"Cat-A\s+from\s+commit",
    r"Cat-A\s+previously\s+log(?:ged|s)",
]


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=20, check=False,
        )
        return r.stdout
    except Exception as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return ""


def get_commit_text(sha: str = "HEAD") -> str:
    """Return commit message + staged-or-committed diff for the sha."""
    msg = _git(["log", "-1", "--format=%B", sha])
    diff = _git(["show", "--stat", sha])
    return msg + "\n" + diff


def _is_in_retrospective_window(text: str, match_start: int, match_end: int) -> bool:
    """A forbidden phrase used in a retrospective sentence ("closes
    the Cat-A from prior commit", "closing the previously-logged
    follow-up") refers to past work being CLOSED, not a new flag
    being deferred. Exempt those.

    We check ±200 chars around the match for any retrospective
    closure verb pattern. Wider window than upstream-only because
    English allows "the Cat-A follow-up that closed in commit X"
    where the closure verb sits AFTER the phrase."""
    window_start = max(0, match_start - 200)
    window_end = min(len(text), match_end + 200)
    window = text[window_start:window_end]

    # Closure verbs near the phrase = retrospective context.
    # "closes Cat-A" / "closing the Cat-A" / "closed the previous Cat-A"
    closure_verbs = (
        r"\bclos(?:e|es|ed|ing|ure)\b",
        r"\bresolv(?:e|es|ed|ing)\b",
        r"\bship(?:ped|ping)\b\s+(?:the\s+)?(?:fix|closure)",
        r"\bfix(?:ed|ing)\b\s+(?:the\s+)?(?:Cat-A|follow-up|deferred)",
    )
    for v in closure_verbs:
        if re.search(v, window, re.IGNORECASE):
            return True

    for marker in RETROSPECTIVE_MARKERS:
        if re.search(marker, window, re.IGNORECASE):
            return True
    return False


def _is_in_audit_definition(text: str, match_start: int) -> bool:
    """If the phrase appears inside a list of forbidden phrases —
    in CLAUDE.md §20 itself, in this script's FORBIDDEN_PHRASES /
    R_BLOCKER_LABELS / RETROSPECTIVE_MARKERS list, in the §20 memory
    file, or in a markdown blockquote enumerating the phrases — it's
    a self-reference, not a real flag.

    Heuristics (any one matches → exempt):
      (a) the matched line is a Python regex literal (starts with
          `r"` or `r'` after whitespace/+),
      (b) the matched line is a markdown blockquote (starts with `>`)
          containing `·` separators (the §20.2 phrase list),
      (c) the file path of this hit (best-effort) is the audit
          script or CLAUDE.md or the §20 memory file,
      (d) FORBIDDEN_PHRASES / Forbidden phrases / §20 / R-blocker /
          R-fix / R-disprove appears within ±600 chars of the match.
    """
    # Line containing the match
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_start)
    line_end = line_end if line_end != -1 else len(text)
    line = text[line_start:line_end]

    # (a) Python regex literal line — `+    r"loggable",` style
    if re.match(r'^\+?\s*r["\']', line):
        return True

    # (b) markdown blockquote with `·` separator → §20.2 forbidden-phrase list
    stripped = line.lstrip("+ ").lstrip()
    if stripped.startswith(">") and "·" in stripped:
        return True

    # (d) wider context — ±600 chars — for marker words
    window_start = max(0, match_start - 600)
    window_end = min(len(text), match_start + 600)
    window = text[window_start:window_end]
    if re.search(
        r"FORBIDDEN_PHRASES|R_BLOCKER_LABELS|RETROSPECTIVE_MARKERS|"
        r"Forbidden\s+phrases|§\s*(?:19\.1|20)|R-blocker|R-fix|R-disprove|"
        r"reproduction\s+law|how\s+this\s+rule\s+was\s+born|"
        r"bug-fix\s+reproduction",
        window,
        re.IGNORECASE,
    ):
        return True

    return False


def has_r_blocker_label_nearby(text: str, match_start: int, match_end: int) -> bool:
    """Check ±200 chars around the matched flag for an explicit
    R-blocker label per §20.1."""
    window_start = max(0, match_start - 200)
    window_end = min(len(text), match_end + 200)
    window = text[window_start:window_end]
    for label in R_BLOCKER_LABELS:
        if re.search(label, window, re.IGNORECASE):
            return True
    return False


def scan(text: str) -> list[tuple[str, int, str]]:
    """Return list of (phrase, line_no, snippet) for every UNRESOLVED
    flag in text. A flag is unresolved when it lacks an R-blocker
    label nearby and is not inside a retrospective window."""
    unresolved: list[tuple[str, int, str]] = []
    for phrase in FORBIDDEN_PHRASES:
        for m in re.finditer(phrase, text, re.IGNORECASE):
            if _is_in_retrospective_window(text, m.start(), m.end()):
                continue
            if _is_in_audit_definition(text, m.start()):
                continue
            if has_r_blocker_label_nearby(text, m.start(), m.end()):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line_end = line_end if line_end != -1 else len(text)
            snippet = text[line_start:line_end].strip()[:140]
            unresolved.append((m.group(0), line_no, snippet))
    return unresolved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", default="HEAD",
                    help="commit sha to scan (default: HEAD)")
    ap.add_argument("--text", default=None,
                    help="scan arbitrary text instead of a commit")
    ap.add_argument("--text-file", default=None,
                    help="scan text from a file (avoids arg-list overflow on huge diffs)")
    ap.add_argument("--strict", action="store_true", default=True,
                    help="exit 1 on any unresolved flag (default)")
    ap.add_argument("--lenient", action="store_true",
                    help="print findings but exit 0")
    args = ap.parse_args()

    if args.text_file is not None:
        with open(args.text_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif args.text is not None:
        text = args.text
    else:
        text = get_commit_text(args.commit)
    if not text.strip():
        print("audit_unresolved_flags: nothing to scan.")
        return 0

    findings = scan(text)

    if not findings:
        print("✅ audit_unresolved_flags: no unresolved flags. Turn closes cleanly.")
        return 0

    print(f"⚠ audit_unresolved_flags: {len(findings)} unresolved flag(s) detected:")
    print()
    for phrase, line_no, snippet in findings:
        print(f"  L{line_no}: «{phrase}»")
        print(f"        {snippet}")
        print()
    print("Per CLAUDE.md §20.1, every flag must be one of:")
    print("  (R-fix)              — fixed in same turn (drop phrase)")
    print("  (R-disprove)         — disproven with cited evidence (drop phrase)")
    print("  (R-blocker:<class>)  — held by hard blocker, where <class> is one of:")
    print("                          founder-domain | tier_2-approval |")
    print("                          external-dep | sprint>1d")
    print()
    print("Rewrite the commit message (or the turn-close reply) so each")
    print("forbidden phrase is paired with an explicit R-blocker label,")
    print("OR fix the underlying concern in the same turn.")
    print()
    print("This audit is the §20 brutal-honesty law. Bypassing it requires")
    print("`--lenient` AND an explicit founder-approved exception.")

    if args.lenient:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
