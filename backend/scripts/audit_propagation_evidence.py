#!/usr/bin/env python3
"""audit_propagation_evidence.py — Pin §21 macchia d'olio mandate.

Doctrine alone failed on 2026-05-06: §19 and §20 already mandated
brutal honesty + multidim sweep, but the operator-shop email leak
shipped anyway because no MECHANICAL gate forced the propagation
discipline. This audit closes that gap.

Fires at commit-msg stage. For non-trivial commits with close-claim
phrases ("10/10", "killer", "shipped", "complete", "closed",
"closes", "fixes", etc.), requires the commit body to contain
explicit propagation evidence:

  - "Sibling sweep:" or "Sibling hunt:" or "Macchia d'olio:" with
    file:line citations OR explicit "0 siblings found after grep
    of <pattern>".
  - At least 2 of the 3 DA lenses cited:
      "Internal —" / "Investor-CTO —" / "Competitor-CTO —"
  - "Preventer" or "Audit" or "Test" mention proving structural
    hardening shipped alongside the fix (not just point-fix).

Trivial commits (typo / formatting / docs-only / single-line fix)
are skipped — declared via "trivial:" prefix in commit message
OR by file count <= 1 + <= 5 lines changed.

Operator override: --lenient flag treats violations as warnings.
Reserved for emergencies; default mode blocks.

Exit codes:
  0 — non-trivial commit has propagation evidence (or commit is trivial)
  1 — non-trivial commit missing macchia d'olio evidence

# invariant-eligible: false
# Reason: commit-msg gate, not a runtime invariant.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


_CLOSE_CLAIM_RE = re.compile(
    r"\b(?:10/10|11/10|killer|shipped|complete|closed|closes|fixes?|"
    r"resolves?|hardening|sweep)\b",
    re.IGNORECASE,
)

_TRIVIAL_PREFIX_RE = re.compile(
    r"^\s*(?:typo|fmt|format|docs?|chore|wip|debug)[:\s(]",
    re.IGNORECASE,
)

_SIBLING_SWEEP_RE = re.compile(
    r"sibling\s*(?:sweep|hunt)|macchia\s*d['']?\s*olio|"
    r"propagation\s*evidence|propagation\s*map|"
    r"siblings?\s*(?:found|hunted|grepped|swept)|"
    r"0\s+siblings?\s+(?:found|after)",
    re.IGNORECASE,
)

_DA_LENS_RE = re.compile(
    r"(?:Internal\s*[—\-:]|Investor[\s\-]*CTO\s*[—\-:]|"
    r"Competitor[\s\-]*CTO\s*[—\-:]|Devil['']?s?\s*Advocate)",
    re.IGNORECASE,
)

_PREVENTER_RE = re.compile(
    r"\b(?:preventer|audit\b|new\s+test|new\s+audit|"
    r"audit_\w+\.py|tests?/test_\w+\.py|invariant_monitor)",
    re.IGNORECASE,
)


def _read_msg(path: str | None) -> str:
    candidates = []
    if path:
        candidates.append(path)
    candidates.append("/opt/wishspark/.git/COMMIT_EDITMSG")
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
                lines = [ln for ln in raw.split("\n") if not ln.lstrip().startswith("#")]
                text = "\n".join(lines).strip()
                if text:
                    return text
            except Exception:
                continue
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%B"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _diff_size() -> tuple[int, int]:
    """Return (file_count, total_added_lines) for the staged commit."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--numstat"],
            cwd="/opt/wishspark",
            stderr=subprocess.DEVNULL,
        )
        text = out.decode("utf-8", errors="replace").strip()
    except Exception:
        return (0, 0)
    if not text:
        # Already-committed: read HEAD
        try:
            out = subprocess.check_output(
                ["git", "show", "--numstat", "--pretty=", "HEAD"],
                cwd="/opt/wishspark",
                stderr=subprocess.DEVNULL,
            )
            text = out.decode("utf-8", errors="replace").strip()
        except Exception:
            return (0, 0)
    files = 0
    added = 0
    for line in text.split("\n"):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        try:
            added += int(parts[0])
        except ValueError:
            pass
    return (files, added)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msg-file", default=None)
    parser.add_argument(
        "--lenient", action="store_true",
        help="Warn instead of block. Reserved for emergencies.",
    )
    args = parser.parse_args()

    msg = _read_msg(args.msg_file)
    if not msg.strip():
        print("audit_propagation_evidence: skip — empty commit message")
        return 0

    first_line = msg.split("\n", 1)[0]

    # Trivial commit prefix → skip
    if _TRIVIAL_PREFIX_RE.search(first_line):
        print(f"audit_propagation_evidence: skip — trivial commit ({first_line[:60]})")
        return 0

    # Trivial size → skip (very small commits don't need full sweep)
    files, added = _diff_size()
    if files <= 1 and added <= 5:
        print(
            f"audit_propagation_evidence: skip — trivial size "
            f"({files} file, {added} lines added)"
        )
        return 0

    # Close-claim phrase triggers the strict gate
    has_close_claim = bool(_CLOSE_CLAIM_RE.search(msg))
    if not has_close_claim:
        print(
            "audit_propagation_evidence: OK — no close-claim phrase, "
            "macchia d'olio evidence not enforced"
        )
        return 0

    # Strict checks
    has_sibling_sweep = bool(_SIBLING_SWEEP_RE.search(msg))
    da_lenses = _DA_LENS_RE.findall(msg)
    has_preventer = bool(_PREVENTER_RE.search(msg))

    missing: list[str] = []
    if not has_sibling_sweep:
        missing.append('Sibling sweep / Macchia d\'olio (cite file:line OR "0 siblings after grep of <pattern>")')
    if len(da_lenses) < 2:
        missing.append(
            f"Triple DA — only {len(da_lenses)} lens(es) cited; need ≥2 of "
            "Internal — / Investor-CTO — / Competitor-CTO —"
        )
    if not has_preventer:
        missing.append("Preventer wiring (audit script / test / invariant_monitor entry)")

    if missing:
        label = "WARN" if args.lenient else "FAIL"
        print(f"audit_propagation_evidence: {label} — close-claim commit missing §21 evidence:")
        for m in missing:
            print(f"  · {m}")
        print(
            "\nPer CLAUDE.md §21 (top-1 CTO macchia d'olio mandate):\n"
            "  Every close-claim commit ('10/10' / 'killer' / 'shipped' /\n"
            "  'closes' / 'fixes' / 'sweep') MUST include in the body:\n"
            "    1. Sibling sweep evidence — grep cited OR '0 siblings found'.\n"
            "    2. ≥2 of 3 DA lenses (Internal / Investor-CTO / Competitor-CTO)\n"
            "       with cited evidence per lens.\n"
            "    3. Preventer wiring shipped alongside the fix.\n"
            "\nReason: doctrine alone (§19/§20) failed 2026-05-06. The mechanical\n"
            "gate forces 'top-1 CTO' default, not opt-in.\n"
            "\nFix: rewrite the commit body to include the missing evidence,\n"
            "OR drop the close-claim phrase if the commit is intentionally a\n"
            "narrow fix. Use --lenient for emergencies (founder-approved only).\n"
        )
        return 0 if args.lenient else 1

    print(
        f"audit_propagation_evidence: OK — close-claim commit has §21 "
        f"evidence (sibling sweep + {len(da_lenses)} DA lens(es) + preventer)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
