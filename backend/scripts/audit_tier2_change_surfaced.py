#!/usr/bin/env python3
"""audit_tier2_change_surfaced.py — TIER_2 commit-msg gate.

CLAUDE.md §10 + §1.6 stop #2: TIER_2 files require EXPLICIT fresh
founder approval per change. Even under session-scoped approval, the
commit message MUST declare which TIER_2 files are changing — same
discipline boundary as the TIER_1 gate (audit_tier1_change_surfaced.py),
but for the strict-approval TIER_2 list.

Born 2026-05-11 after the founder surfaced that three same-session
commits modified app/services/gdpr_processor.py (TIER_2) without
TIER_2 disclosure:
  c67df80 (BI Builder, added bi_saved_queries to shop_redact)
  fc02cab (hardening Redis purge)  — marked TIER_1, was TIER_2
  df77882 (refactor templates)      — no marker
The TIER_1 audit fired on fc02cab but the TIER_1 marker WRONGLY satisfied
the doctrine for a TIER_2 file. This gate closes that class.

Marker forms accepted (any of):
    "TIER_2: app/services/gdpr_processor.py"
    "TIER_2: gdpr_processor" (bare basename)
    "TIER_2 modification surfaced: <free text>"
    "TIER_2 fresh approval: <founder directive citation>"
    "TIER_2 session-scoped approval: <sprint memo>"
    "TIER_2 emergency override: <reason>"

Exit codes:
  0 — no TIER_2 files in change OR all TIER_2 changes are surfaced
  1 — TIER_2 file modified without explicit marker

# invariant-eligible: false — runs at commit-msg stage, not preflight
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


# Pattern derived from CLAUDE.md §10 TIER_2 list. Keep in sync with
# the doctrine — if the list there changes, this regex changes too.
_TIER_2_PATTERNS = [
    re.compile(r"^app/core/token_crypto\.py$"),
    re.compile(r"^app/core/merchant_session\.py$"),
    re.compile(r"^app/api/shopify_oauth\.py$"),
    re.compile(r"^app/api/billing\.py$"),
    re.compile(r"^app/core/deps\.py$"),
    re.compile(r"^app/api/webhooks\.py$"),
    re.compile(r"^app/services/order_ingestion\.py$"),
    re.compile(r"^app/services/gdpr_processor\.py$"),
    re.compile(r"^migrations/.*$"),
    re.compile(r"^ecosystem\.config\.js$"),
    re.compile(r"^\.env$"),
    re.compile(r"^deploy\.sh$"),
    # `backend/` prefix variant — git diff runs from repo root.
    re.compile(r"^backend/app/core/token_crypto\.py$"),
    re.compile(r"^backend/app/core/merchant_session\.py$"),
    re.compile(r"^backend/app/api/shopify_oauth\.py$"),
    re.compile(r"^backend/app/api/billing\.py$"),
    re.compile(r"^backend/app/core/deps\.py$"),
    re.compile(r"^backend/app/api/webhooks\.py$"),
    re.compile(r"^backend/app/services/order_ingestion\.py$"),
    re.compile(r"^backend/app/services/gdpr_processor\.py$"),
    re.compile(r"^backend/migrations/.*$"),
    re.compile(r"^backend/\.env$"),
    re.compile(r"^backend/deploy\.sh$"),
]

_TIER_2_MARKER_RE = re.compile(
    r"\bTIER_?2\b[^a-zA-Z0-9].{0,200}",
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
    """Return only the files matching a TIER_2 pattern."""
    hits: list[str] = []
    for f in files:
        for pat in _TIER_2_PATTERNS:
            if pat.match(f):
                hits.append(f)
                break
    return hits


def _has_marker(msg: str, tier2_files: list[str]) -> tuple[bool, str]:
    if not msg:
        return False, "empty commit message"
    if not _TIER_2_MARKER_RE.search(msg):
        return False, "no 'TIER_2' marker found in message"
    broad_markers = (
        re.compile(r"TIER_?2\s+(?:modification|change)\s+surfaced", re.IGNORECASE),
        re.compile(r"TIER_?2\s+emergency\s+override", re.IGNORECASE),
        re.compile(r"TIER_?2\s+session[- ]scoped\s+approval", re.IGNORECASE),
        re.compile(r"TIER_?2\s+fresh\s+approval", re.IGNORECASE),
    )
    if any(p.search(msg) for p in broad_markers):
        return True, "broad TIER_2 surfacing declaration found"
    for f in tier2_files:
        basename = os.path.basename(f).rsplit(".", 1)[0]
        per_file = re.compile(
            rf"TIER_?2[\s:]+(?:[^\n]*?{re.escape(basename)}|[^\n]*?{re.escape(f)})",
            re.IGNORECASE,
        )
        if per_file.search(msg):
            return True, f"explicit TIER_2 marker for {basename}"
    return False, "TIER_2 keyword present but no per-file or broad declaration"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msg-file", default=None,
                        help="Path to commit message file (commit-msg hook $1)")
    parser.add_argument("--strict", action="store_true",
                        help="No-op shim for compat.")
    args = parser.parse_args()

    files = _staged_files()
    tier2 = _classify(files)
    if not tier2:
        print("audit_tier2_change_surfaced: OK — no TIER_2 files in change")
        return 0

    msg = _read_msg(args.msg_file)
    ok, reason = _has_marker(msg, tier2)
    if ok:
        print(
            f"audit_tier2_change_surfaced: OK — {reason} "
            f"(TIER_2 files modified: {len(tier2)})"
        )
        return 0

    print("audit_tier2_change_surfaced: FAIL — TIER_2 modification without surfacing")
    print(f"  TIER_2 files in this change ({len(tier2)}):")
    for f in tier2:
        print(f"    - {f}")
    print(f"  Reason: {reason}")
    print(
        "\n  TIER_2 = strict-approval list per CLAUDE.md §10. Per §1.6 stop\n"
        "  #2, every TIER_2 change requires EXPLICIT fresh founder approval.\n"
        "  Add ONE of these to the commit body:\n"
        '    "TIER_2: <file_basename>"  (per-file disclosure)\n'
        '    "TIER_2 fresh approval: <founder directive citation>"\n'
        '    "TIER_2 modification surfaced: <reason>"\n'
        '    "TIER_2 session-scoped approval: <sprint memo>"\n'
        '    "TIER_2 emergency override: <reason>"\n'
        "\n  Session-scoped approval is acceptable for TIER_2 only when\n"
        "  the founder has explicitly bundled the file under the sprint\n"
        "  scope. Implicit autonomy under 'procedi' does NOT cover TIER_2.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
