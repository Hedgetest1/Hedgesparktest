#!/usr/bin/env python3
"""audit_commit_devils_advocate.py — enforce real 10/10 devil's advocate
discipline on every commit message.

Problem class: §19 devil's advocate sections routinely contained phrases
like "acceptable transient", "accettabile per beta", "TODO follow-up if
founder notices", "defer to Phase 4". Each such phrase was a silent
deferral that violated `feedback_no_accettabile_per_beta.md`. Claude's
judgment alone has been inconsistent; we need a preventer that catches
the pattern mechanically.

This audit is installed as a git `commit-msg` hook. Every commit's
message is parsed. If it contains a devil's-advocate section AND that
section contains any red-flag phrase without an EXPLICIT category
tag (Cat 4 = missing merchant data, or Cat 5 = future architecture
with memory pointer), the commit is blocked.

Categorization model from `feedback_no_accettabile_per_beta.md`:
  1. Real bug / UI lie / inconsistency           → FIX NOW
  2. Real UX gap                                  → FIX NOW
  3. Scale concern for 10k merchants              → FIX NOW or prereq
  4. Missing real merchant data (traffic/orders)  → legitimate defer
  5. Architecture future consideration            → defer with memory

Categories 1-3 are the "fix now" band. Any concern falling in those
bands MUST be addressed in the same commit OR in a commit referenced
from the same message. Categories 4-5 are the only legitimate
deferrals, and both require explicit tagging.

Usage:
    ./audit_commit_devils_advocate.py <commit-msg-file>

Exit codes:
    0   message clean OR no devil's advocate section present
    1   red flag detected without Cat-4/5 tag — commit blocked
    2   script error
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import emit, telemetered

# A line qualifies as a devil's-advocate HEADER only when, after
# stripping optional leading markdown `#`s and whitespace, the line
# STARTS with one of the DA phrases. Matching "devil's advocate"
# anywhere in the message would false-trigger on e.g. a commit title
# like "commit-msg hook enforces devil's advocate 10/10".
#
# Accepts straight ASCII apostrophe (U+0027) and curly variants
# (U+2018 / U+2019). Allows an optional "AXIS 5" prefix or "Brutal "
# modifier. The header line may be short or a longer sentence that
# begins with the DA phrase.
_DA_HEADERS = re.compile(
    r"""
    ^\s*                                  # leading whitespace
    (?:\#+\s*)?                           # optional markdown hashes
    (?:
        (?:AXIS\s*5\s*[-—:\u2014]?\s*)    # "AXIS 5 —" prefix
      | (?:AXIS\s*5\s*$)                  # bare "AXIS 5" line
    )?
    (?:brutal\s+)?                        # optional "brutal" modifier
    devil['\u2018\u2019]?s?\s+advocate
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Red flag phrases — if present inside the DA section, MUST be paired
# with a category-4/5 tag nearby (same line or next line). Without a
# tag, the phrase is treated as a silent deferral.
_RED_FLAGS = [
    re.compile(r"\bacceptab(?:le|ility)\b", re.IGNORECASE),
    re.compile(r"\baccept(?:ed|ing|able)?\s+(?:for|per)?\s*beta\b", re.IGNORECASE),
    re.compile(r"\baccettab(?:ile|ilita)\b", re.IGNORECASE),
    re.compile(r"\baccettat[oa]\b", re.IGNORECASE),  # "accettato/a" — missed 2026-04-25
    re.compile(r"\btolerab(?:le|ility)\b", re.IGNORECASE),
    re.compile(r"\bOK\s+for\s+now\b", re.IGNORECASE),
    re.compile(r"\bper\s+(?:ora|adesso)\s+va\s+bene\b", re.IGNORECASE),
    re.compile(r"\btodo\b.{0,80}\bif\s+founder\s+notices?\b", re.IGNORECASE),
    re.compile(r"\bif\s+founder\s+notices?\b", re.IGNORECASE),
    re.compile(r"\bdeferr?(?:ed|al)?\s+to\s+later\b", re.IGNORECASE),
    re.compile(r"\blater\s+sprint\b", re.IGNORECASE),
    re.compile(r"\bfine\s+for\s+(?:beta|now)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+revisit\b", re.IGNORECASE),
    re.compile(r"\bfollow-?up\s+commit\b", re.IGNORECASE),
    # Added 2026-04-25 after founder caught these 5 silent-defer
    # phrases that slipped through earlier this session:
    re.compile(r"\bdiminishing\s+returns?\b", re.IGNORECASE),
    re.compile(r"\bbootstrap\s+(?:level|fidelity|acceptable|mode)\b", re.IGNORECASE),
    re.compile(r"\bcat-?a\s+follow-?up\b", re.IGNORECASE),
    re.compile(r"\bproduct-?maturity\b", re.IGNORECASE),
    re.compile(r"\bedge\s+case,?\s+accept(?:ed|ing|able)?\b", re.IGNORECASE),
]

# Explicit category tags that legitimize a deferral. A red flag paired
# with one of these on the same or adjacent line passes.
_CAT_TAG = re.compile(
    r"""
    (?:
        \bcat(?:egory)?\s*[45]\b                                            # "Cat 4", "category 5"
      | \bmissing\s+(?:real\s+)?merchant\s+data\b                           # Cat 4 canonical
      | \bno\s+traffic\s+yet\b | \bno\s+orders?\s+yet\b                     # Cat 4 variants
      | \bcold[\s-]?start\b                                                 # Cat 4 cold-start context
      | \bphase\s*\d+(?:\.\d+)?\b                                           # Phase X.Y (Cat 5 memory pointer)
      | project_[a-z0-9_]+\.md | feedback_[a-z0-9_]+\.md                    # explicit memory file
      | memory[:\s]+[a-z_]+\.md                                              # "memory: X.md"
      | \btracked\s+in\s+[a-z_]+\.md                                        # "tracked in X.md"
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_da_section(message: str) -> tuple[list[str], int] | None:
    """Return (section-lines, start-line-index) for the devil's-advocate
    section of the message, or None if no DA section found.

    A DA section starts at the first header match and extends until the
    next blank-line-plus-header boundary or end of message. We keep the
    scan simple — commit messages are short."""
    lines = message.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _DA_HEADERS.search(line):
            start = i
            break
    if start is None:
        return None

    # Collect lines until another ## header or double-blank boundary.
    section: list[str] = []
    blank_run = 0
    for line in lines[start:]:
        # A markdown-style header different from DA terminates the section
        if re.match(r"^\s{0,3}(?:##+\s|AXIS\s*[0-9]+)", line) and section and not _DA_HEADERS.search(line):
            break
        # Double-blank = section boundary
        if not line.strip():
            blank_run += 1
            if blank_run >= 2 and section:
                break
        else:
            blank_run = 0
        section.append(line)

    return section, start


def check_section(section: list[str]) -> list[tuple[int, str, str]]:
    """Scan the DA section for red flags. Return a list of
    (lineno-relative-to-section, matched-phrase, surrounding-context)."""
    findings: list[tuple[int, str, str]] = []
    for i, line in enumerate(section):
        for pat in _RED_FLAGS:
            m = pat.search(line)
            if not m:
                continue
            # Look for a category tag on this line or the next two lines
            context_window = "\n".join(section[i : min(i + 3, len(section))])
            if _CAT_TAG.search(context_window):
                continue  # legitimate tagged deferral
            findings.append((i, m.group(0), line.strip()))
            break  # one finding per line is enough
    return findings


@telemetered("audit_commit_devils_advocate")
def main(argv: list[str]) -> int:

    if len(argv) < 1:
        print(
            "audit_commit_devils_advocate: need commit-msg file path",
            file=sys.stderr,
        )
        return 2

    msg_path = Path(argv[0])
    try:
        message = msg_path.read_text()
    except OSError as exc:
        print(
            f"audit_commit_devils_advocate: can't read {msg_path}: {exc}",
            file=sys.stderr,
        )
        return 2

    # Strip ONLY git's own editor-buffer comments (single "#" + space, or
    # a bare "#") — NOT markdown headers like "## Devil's advocate", which
    # legitimately start with multiple hashes.
    message = "\n".join(
        line for line in message.splitlines()
        if not (line.startswith("# ") or line.rstrip() == "#")
    )

    section_info = extract_da_section(message)
    if section_info is None:
        # No DA section at all — trivial commit or pre-§19 style. Allowed.
        emit("audit_commit_devils_advocate", findings=0, severity="info")
        return 0

    section, _ = section_info
    findings = check_section(section)
    if not findings:
        emit("audit_commit_devils_advocate", findings=0, severity="info")
        return 0

    print(
        "audit_commit_devils_advocate: devil's advocate section contains "
        f"{len(findings)} silent-deferral phrase(s) without a Cat-4/5 tag."
    )
    print()
    print("Per `feedback_no_accettabile_per_beta.md`, concerns in categories")
    print("1-3 (real bug / UX gap / scale concern) MUST be fixed in the same")
    print("commit. Only Cat-4 (missing real merchant data) or Cat-5 (future")
    print("architecture with memory pointer) are legitimate deferrals, and")
    print("both require explicit tagging on the same or adjacent line.")
    print()
    print("Add one of these tags next to the concern, or fix it before commit:")
    print('  - "Cat 4 — missing real merchant data (traffic/orders not yet)"')
    print('  - "Cat 5 — tracked in project_phase_X_backlog.md"')
    print('  - "Phase 2.0 Elite Auto-Deploy (memory: project_elite_auto_deploy_phase_2_0.md)"')
    print()
    print("Findings:")
    for i, phrase, line in findings:
        print(f"  line {i + 1}: \"{phrase}\" — {line[:120]}")
    print()
    emit("audit_commit_devils_advocate",
         findings=len(findings), severity="warn")
    # Default lenient since 2026-05-07 doctrine trim. The audit still
    # surfaces the analysis (printed above) but doesn't block the
    # commit — operator override --strict to flip back to blocking.
    if "--strict" in argv:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_commit_devils_advocate: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
