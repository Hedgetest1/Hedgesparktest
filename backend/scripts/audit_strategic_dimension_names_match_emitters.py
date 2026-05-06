#!/usr/bin/env python3
"""audit_strategic_dimension_names_match_emitters.py — Pin G3.

The Telegram strategic-only gate in
`app/services/system_health_synthesizer.py::_is_strategic_critical`
suppresses every CTO signal whose `HealthDimension.name` is not in
`_STRATEGIC_DIMENSIONS`. If the constant drifts from the emitter
function names (e.g. someone renames `_assess_memory` → name="ram"
without updating the constant), the gate silently suppresses ALL
signals — founder gets ZERO Telegram pings on real capacity blowout.

This audit pins the contract:
    every name in _STRATEGIC_DIMENSIONS MUST appear as `name="..."`
    in some `_assess_*` function in system_health_synthesizer.py.

Static parse only — no DB or import needed. Sufficient because the
synthesizer is small and the names are literal strings in code.

Exit codes:
  0 — every strategic name has a matching emitter
  1 — at least one strategic name has no emitter (silent-failure regression)

# invariant-eligible: true
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTHESIZER = ROOT / "app" / "services" / "system_health_synthesizer.py"


def _extract_strategic_constant(src: str) -> set[str]:
    """Find _STRATEGIC_DIMENSIONS = frozenset({...}) and return its values."""
    m = re.search(
        r"_STRATEGIC_DIMENSIONS\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src,
    )
    if not m:
        return set()
    body = m.group(1)
    names: set[str] = set()
    for tok in re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", body):
        names.add(tok)
    return names


def _extract_emitted_dim_names(src: str) -> set[str]:
    """Find every HealthDimension(...name="...") in the synthesizer."""
    names: set[str] = set()
    # Match name="..." or name='...' inside HealthDimension( ... ) constructions.
    # Synthesizer uses the keyword form consistently.
    for m in re.finditer(
        r"return\s+HealthDimension\([^)]*?name\s*=\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]",
        src,
        flags=re.DOTALL,
    ):
        names.add(m.group(1))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="No-op shim for invariant_monitor forward-compat.",
    )
    parser.parse_args()

    if not SYNTHESIZER.exists():
        print(f"audit_strategic_dimension_names_match_emitters: NOT FOUND {SYNTHESIZER}")
        return 1

    src = SYNTHESIZER.read_text()
    declared = _extract_strategic_constant(src)
    emitted = _extract_emitted_dim_names(src)

    if not declared:
        print(
            "audit_strategic_dimension_names_match_emitters: FAIL — "
            "_STRATEGIC_DIMENSIONS not found or empty in synthesizer"
        )
        return 1

    missing = declared - emitted
    if missing:
        print(
            "audit_strategic_dimension_names_match_emitters: FAIL — "
            f"strategic names with no emitter: {sorted(missing)}"
        )
        print(f"  declared: {sorted(declared)}")
        print(f"  emitted : {sorted(emitted)}")
        print(
            "  Each name in _STRATEGIC_DIMENSIONS must match a "
            "HealthDimension(name=...) returned by some _assess_* function. "
            "Without the match, _is_strategic_critical() silently suppresses "
            "every Telegram signal — founder gets ZERO pings on real "
            "capacity/spend critical."
        )
        return 1

    # Optional reverse-direction sanity: extra emitter dim with no operational
    # gate is fine (workers/pipeline/etc. are intentionally non-strategic).
    print(
        f"audit_strategic_dimension_names_match_emitters: OK — "
        f"{len(declared)} strategic names ({sorted(declared)}) "
        f"all match emitters."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
