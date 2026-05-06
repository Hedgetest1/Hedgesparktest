#!/usr/bin/env python3
"""audit_telegram_allowlist_ground_truth.py — Pin G2.

Every entry in `_TELEGRAM_STRATEGIC_ALLOWLIST` (in
`app/services/on_alert_responder.py`) MUST have a real emitter
somewhere in the codebase. The 2026-05-05 sprint shipped 8 entries
of which 7 were aspirational (no emitter). Founder direttiva
"no theater, no half-truths, no hollow stubs" (CLAUDE.md §2 rule 2)
forbids that.

This audit:
    1. Parses the active allowlist + the _FUTURE_STRATEGIC_RESERVED set.
    2. For each ACTIVE entry, greps app/ for an emitter — at minimum
       a string-literal occurrence of the alert_type as a write_alert
       parameter OR as a column-comparison.
    3. Fails if any active entry has no emitter (silent
       founder-page-coverage lie) OR if any reserved entry has been
       silently moved to active without its emitter shipping.

Static-parse only — no DB or import. Sufficient because the
allowlist is a small literal frozenset and emitter sites use
literal alert_type strings.

Exit codes:
  0 — every active allowlist entry has an emitter
  1 — at least one active entry has no emitter (phantom)

# invariant-eligible: true
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
RESPONDER = APP / "services" / "on_alert_responder.py"


def _extract_frozenset_names(src: str, var_name: str) -> set[str]:
    """Find <var_name> = frozenset({...}) and return its string entries."""
    m = re.search(
        rf"{re.escape(var_name)}\s*=\s*frozenset\(\s*\{{(.*?)\}}\s*\)",
        src,
        flags=re.DOTALL,
    )
    if not m:
        return set()
    body = m.group(1)
    names: set[str] = set()
    for tok in re.findall(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", body):
        names.add(tok)
    return names


def _has_emitter(alert_type: str) -> tuple[bool, list[str]]:
    """Return (True, hits) if any file in app/ (excluding the
    responder itself) references this alert_type as a string literal
    in a context that looks like an emitter site (write_alert call,
    SQL filter, comparison)."""
    try:
        result = subprocess.run(
            [
                "grep", "-rn",
                "--include=*.py",
                f"alert_type.*['\"]{alert_type}['\"]",
                str(APP),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        out = result.stdout.strip()
    except Exception:
        return False, []
    if not out:
        return False, []
    hits: list[str] = []
    for line in out.splitlines():
        # Exclude the allowlist declaration itself (lives in
        # on_alert_responder.py) — it's the doctrine, not an emitter.
        if "on_alert_responder.py" in line and "TELEGRAM_STRATEGIC" in line:
            continue
        # Exclude lines that are just commented-out or doc string.
        stripped = line.split(":", 2)[-1].lstrip()
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue
        hits.append(line)
    return bool(hits), hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="No-op shim for invariant_monitor forward-compat.",
    )
    parser.parse_args()

    if not RESPONDER.exists():
        print(f"audit_telegram_allowlist_ground_truth: NOT FOUND {RESPONDER}")
        return 1

    src = RESPONDER.read_text()
    active = _extract_frozenset_names(src, "_TELEGRAM_STRATEGIC_ALLOWLIST")
    reserved = _extract_frozenset_names(src, "_FUTURE_STRATEGIC_RESERVED")

    if not active:
        print(
            "audit_telegram_allowlist_ground_truth: FAIL — "
            "_TELEGRAM_STRATEGIC_ALLOWLIST not found or empty"
        )
        return 1

    overlap = active & reserved
    if overlap:
        print(
            "audit_telegram_allowlist_ground_truth: FAIL — entries appear "
            f"in BOTH active and reserved sets: {sorted(overlap)}"
        )
        return 1

    phantoms: list[str] = []
    for entry in sorted(active):
        ok, _ = _has_emitter(entry)
        if not ok:
            phantoms.append(entry)

    if phantoms:
        print(
            "audit_telegram_allowlist_ground_truth: FAIL — active allowlist "
            f"entries with no emitter: {phantoms}"
        )
        print(
            "  Each active entry MUST have a write_alert(...) site OR a "
            "column-comparison against the alert_type. Phantom entries "
            "lie about founder-page coverage. Either ship the emitter in "
            "this commit OR move the entry to _FUTURE_STRATEGIC_RESERVED."
        )
        return 1

    print(
        f"audit_telegram_allowlist_ground_truth: OK — "
        f"{len(active)} active allowlist entry(ies) ({sorted(active)}) "
        f"all have real emitters; {len(reserved)} reserved entry(ies) "
        f"tracked for future wiring."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
