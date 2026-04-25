#!/usr/bin/env python3
"""audit_da_evidence.py — every devil's-advocate lens MUST cite executable evidence.

Born 2026-04-25 night after the founder caught a turn-close where the
DA paragraphs read fine in prose but contained ZERO `grep -n` /
`pytest` / `curl` / `psql` output. Re-running each lens for real
surfaced 50 silent regressions the polished prose had masked.

CLAUDE.md §19 Axis 5 says verbatim: "every lens MUST cite at least one
executable verification — `grep -n`, a `pytest` run, a `curl`, a
`psql` query — and report the output. The strict form per lens is
*Lens N — challenge: X. Evidence: <command> → <output snippet>.
Verdict: Y*". This audit makes the rule mechanically enforced on
commit messages.

Detection model
---------------
1. Locate the devil's-advocate section in the commit message (reuses
   the same _DA_HEADERS regex as `audit_commit_devils_advocate.py`).
2. Find every "Lens N — ..." reference inside the section.
3. For each lens, look for at least one EVIDENCE TOKEN within ±15
   lines:
     - Triple-backtick fenced code block (likely grep/pytest output)
     - `Evidence:` literal tag (the §19 strict form)
     - `$ <command>` shell-prompt convention
     - `grep -n` / `grep -rn` / `git grep`
     - `pytest` followed by a test path or `passed/failed` output
     - `curl` with HTTP method or URL
     - `psql -c` or `psql >` output line
     - `→` arrow connecting command to output (our shorthand)

If a lens has NO evidence token nearby, the audit fails — the lens is
prose without proof, which is the failure mode this rule prevents.

Mode
----
- Default: scan the commit message file passed as arg (commit-msg hook).
- `--text-file`: read text from a file (preflight integration).
- `--text`: scan inline text (debugging).
- Exit 1 on any unevidenced lens.

Allowed escape hatch: a lens that explicitly disclaims evidence ("no
verification needed because ...") with an explanation can pass. Match
on `(no verification|sanity-only|not applicable)` to honor that, since
some lenses are pure design-trade-off questions where evidence isn't
possible.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from _audit_telemetry_shim import emit, telemetered


# Reuse the DA-header regex shape from audit_commit_devils_advocate.py.
_DA_HEADERS = re.compile(
    r"""
    ^\s*
    (?:\#+\s*)?
    (?:
        (?:AXIS\s*5\s*[-—:—]?\s*)?
        (?:Brutal\s+)?
        Devil['‘’]?s\s+advocate
        |
        Devil-?s-?advocate
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A lens reference. Multiple shapes accepted:
#   - "Lens 1"           (the canonical short form)
#   - "Lens 1 —"         (em-dash variant)
#   - "Lens 1 -"         (hyphen variant)
#   - "Lens N — challenge"  (the §19 strict form)
_LENS_REF = re.compile(
    r"\bLens\s+(\d+)\b",
    re.IGNORECASE,
)

# Evidence tokens. Any of these within ±15 lines of a lens line counts
# as proof that the lens was actually run, not just claimed.
_EVIDENCE_TOKENS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```"),                         # fenced code block
    re.compile(r"\bEvidence\s*:", re.IGNORECASE),
    re.compile(r"^\s*\$\s+\S", re.MULTILINE),   # `$ command` line
    re.compile(r"\bgrep\s+-[a-z]"),             # grep with flags
    re.compile(r"\bgit\s+grep\b"),
    re.compile(r"\bpytest\b.*(?:passed|failed|::)"),  # pytest with test/result
    re.compile(r"\bcurl\b.*(?:http|-X|-s|-o\s|-w\s)"),  # curl with HTTP
    re.compile(r"\bpsql\b.*(?:-c|>)"),
    re.compile(r"\s+→\s+"),                    # arrow connecting cmd → output
    re.compile(r"\bno\s+verification\s+needed", re.IGNORECASE),
    re.compile(r"\bsanity-only\b", re.IGNORECASE),
    re.compile(r"\bnot\s+applicable\b", re.IGNORECASE),
)

_EVIDENCE_WINDOW_LINES = 15


def extract_da_section(message: str) -> tuple[list[str], int] | None:
    lines = message.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _DA_HEADERS.search(line):
            start = i
            break
    if start is None:
        return None
    section: list[str] = []
    blank_run = 0
    for line in lines[start:]:
        if re.match(r"^\s{0,3}(?:##+\s|AXIS\s*[0-9]+)", line) and section and not _DA_HEADERS.search(line):
            break
        if not line.strip():
            blank_run += 1
            if blank_run >= 2 and section:
                break
        else:
            blank_run = 0
        section.append(line)
    return section, start


def find_unevidenced_lenses(section: list[str]) -> list[tuple[int, str]]:
    """For each lens line, check ±15 surrounding lines for evidence."""
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(section):
        m = _LENS_REF.search(line)
        if not m:
            continue
        lens_num = m.group(1)
        # Window around this lens line.
        lo = max(0, i - _EVIDENCE_WINDOW_LINES)
        hi = min(len(section), i + _EVIDENCE_WINDOW_LINES + 1)
        window_text = "\n".join(section[lo:hi])
        if any(tok.search(window_text) for tok in _EVIDENCE_TOKENS):
            continue
        findings.append((i, f"Lens {lens_num}"))
    return findings


def _strip_git_comments(message: str) -> str:
    """Drop pure git-editor comment lines without losing markdown headers."""
    out = []
    for ln in message.splitlines():
        s = ln.lstrip()
        # A bare "#" or "# " comment line — git's editor convention.
        # Keep "##", "###" etc. (markdown headers).
        if s.startswith("#") and not s.startswith("##"):
            continue
        out.append(ln)
    return "\n".join(out)


@telemetered("audit_da_evidence")
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=None, help="commit-msg file path")
    ap.add_argument("--text", default=None, help="scan inline text")
    ap.add_argument("--text-file", default=None,
                    help="scan text from a file (avoids arg-list overflow)")
    args = ap.parse_args(argv)

    if args.text is not None:
        message = args.text
    elif args.text_file is not None:
        message = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    elif args.path is not None:
        message = Path(args.path).read_text(encoding="utf-8", errors="replace")
    else:
        print("audit_da_evidence: need a commit-msg path, --text, or --text-file", file=sys.stderr)
        return 2

    message = _strip_git_comments(message)
    # The audit triggers whenever Lens references appear ANYWHERE in
    # the message — the DA section header is sufficient but not
    # necessary. This catches the "Verification:" / "Lens N — ..."
    # variant where the prose dropped the formal DA header but
    # still claimed lens-by-lens analysis. The original DA-section
    # extractor stays as a way to scope future stricter checks.
    full_lines = message.splitlines()
    lens_line_indices = [i for i, line in enumerate(full_lines) if _LENS_REF.search(line)]

    if not lens_line_indices:
        emit("audit_da_evidence", findings=0, severity=None)
        print("audit_da_evidence: no Lens references to audit (skipped)")
        return 0

    findings: list[tuple[int, str]] = []
    for i in lens_line_indices:
        lo = max(0, i - _EVIDENCE_WINDOW_LINES)
        hi = min(len(full_lines), i + _EVIDENCE_WINDOW_LINES + 1)
        window_text = "\n".join(full_lines[lo:hi])
        if any(tok.search(window_text) for tok in _EVIDENCE_TOKENS):
            continue
        m = _LENS_REF.search(full_lines[i])
        label = f"Lens {m.group(1)}" if m else "Lens"
        findings.append((i, label))
    start = 0  # absolute line numbers since we scan whole message
    emit(
        "audit_da_evidence",
        findings=len(findings),
        severity="warn" if findings else None,
    )

    if not findings:
        print("✅ audit_da_evidence: every lens has executable evidence within ±15 lines")
        return 0

    print(f"⚠ audit_da_evidence: {len(findings)} lens(es) without evidence:")
    for line_idx, label in findings:
        absolute_line = line_idx + 1
        snippet = full_lines[line_idx].strip()[:120]
        print(f"  L{absolute_line}: {label} — «{snippet}»")
    print()
    print("Per CLAUDE.md §19 Axis 5, every devil's-advocate lens MUST cite")
    print("at least one executable verification within ±15 lines. Use the")
    print("§19 strict form:")
    print()
    print("  Lens N — challenge: X. Evidence: <command> → <output snippet>.")
    print("  Verdict: Y")
    print()
    print("Accepted evidence tokens: triple-backtick code blocks,")
    print("`Evidence:` tag, `$ <command>` lines, `grep -n`, `pytest`")
    print("with passed/failed, `curl` with URL, `psql -c`, `→` arrow.")
    print()
    print("If a lens is genuinely a no-evidence design question, write")
    print("an explicit `no verification needed because ...` line.")

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
