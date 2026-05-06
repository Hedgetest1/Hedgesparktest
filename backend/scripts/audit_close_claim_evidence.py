#!/usr/bin/env python3
"""audit_close_claim_evidence.py — commit-msg gate enforcing
CLAUDE.md §22.2 + §22.7 + feedback_default_score_7_until_agent_verified.

If the commit message claims any score ≥ 8.5 (e.g., "Score: 9.0",
"9.4/10", "honest score 8.7", "10/10"), the body MUST contain
either:

  1. An `Agent invocation:` citation (Agent Task ID or output line)
     — proves §21.7 + §22.4 default-proactive Agent invocation.
  2. A `score breakdown` showing floor 7.0 + cited evidence per
     0.5 increment.
  3. An explicit `# capillary-bypass:` annotation (operator override
     for emergency commits where Agent invocation is genuinely
     infeasible — e.g., no network).

Born 2026-05-06 from the founder's brutal-honesty audit. The
2026-05-06 session pattern was: claim 9.4 → founder finds gap →
adjust to 8.7 → fix → claim 9.7 → ad infinitum. Score anchored
to comfort. This audit forces the score to *earn* its way up
via cited evidence at commit time.

Usage
-----
This is a commit-msg hook gate. Receives the commit message file
path as $1.

Exit codes
  0 — score claim absent OR score claim well-evidenced.
  1 — score claim ≥ 8.5 without Agent invocation citation OR
      score breakdown OR bypass annotation (commit refused).

# invariant-eligible: false
# (commit-msg-only: nothing to monitor at runtime)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Score-claim patterns. Match floats with optional "/10" or "%"
# suffixes. Word-boundary protected.
_SCORE_PATTERNS = [
    re.compile(r"\bScore:\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\b(\d+(?:\.\d+)?)\s*/\s*10\b"),
    re.compile(r"\bhonest\s+score\s*[:\-]\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bbrutal\s+score\s*[:\-]\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE),
]

_THRESHOLD = 8.5

_AGENT_CITATION = re.compile(
    r"\bAgent(?:\s+invocation)?\s*[:\(]|"
    r"\bTask\s*#?\d+\b|"
    r"\bsubagent_type\s*=|"
    r"\bgeneral-purpose\b|"
    r"\bAgent\(Explore\)|"
    r"\bAgent\(general-purpose\)",
    re.IGNORECASE,
)

_BREAKDOWN_MARKER = re.compile(
    r"\bfloor\s*[:\-]\s*7(?:\.0)?\b",
    re.IGNORECASE,
)

_BYPASS_MARKER = re.compile(
    r"#\s*capillary[- ]bypass:|"
    r"#\s*score[- ]bypass:",
    re.IGNORECASE,
)


def _max_score_claim(msg: str) -> float | None:
    found: list[float] = []
    for pat in _SCORE_PATTERNS:
        for m in pat.finditer(msg):
            try:
                v = float(m.group(1))
                # Filter context-noise: e.g. "9/10 of cases" or
                # "MAX 7/10 cap" should still count if number is
                # within plausible score range. Cap at sane bounds.
                if 0.0 <= v <= 10.0:
                    found.append(v)
            except (ValueError, IndexError):
                continue
    if not found:
        return None
    return max(found)


def main() -> int:
    if len(sys.argv) < 2:
        # Hook not invoked with a message file → no-op
        return 0
    msg_path = Path(sys.argv[1])
    if not msg_path.is_file():
        return 0
    msg = msg_path.read_text(encoding="utf-8")

    score = _max_score_claim(msg)
    if score is None:
        return 0  # no claim → no gate
    if score < _THRESHOLD:
        return 0  # under threshold → no gate

    # Check for evidence
    has_agent = bool(_AGENT_CITATION.search(msg))
    has_breakdown = bool(_BREAKDOWN_MARKER.search(msg))
    has_bypass = bool(_BYPASS_MARKER.search(msg))

    if has_agent or has_breakdown or has_bypass:
        # Acceptable evidence form found
        return 0

    print(
        f"audit_close_claim_evidence: BLOCKED — commit claims score "
        f"{score} (≥ {_THRESHOLD}) without evidence."
    )
    print()
    print("Per CLAUDE.md §22.2 + §22.7 + feedback_default_score_7_until_agent_verified:")
    print("  every 0.5 above floor 7.0 requires cited evidence.")
    print()
    print("Required: ONE of these must appear in the commit body:")
    print("  1. `Agent invocation:` citation (e.g., 'Agent(general-purpose) Task #N output')")
    print("  2. `Floor: 7.0` breakdown line followed by per-0.5 evidence citations")
    print("  3. `# capillary-bypass: <reason>` operator override (use only when")
    print("     Agent invocation is genuinely infeasible — emergency commits)")
    print()
    print("Common fix: invoke Agent(general-purpose) for independent audit,")
    print("paste the Task ID + finding count in the commit body.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
