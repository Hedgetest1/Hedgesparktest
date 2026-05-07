#!/usr/bin/env python3
"""Audit: brain enrichers registered in pipeline_state.

Born 2026-05-07 closing alert #129083. The fix relies on
`pipeline_state.is_pipeline_dormant()` reading EVERY brain enricher
env var. If a new enricher module ships in 6 months and the
developer forgets to register its env var with pipeline_state,
dormancy detection drifts: the new enricher could be ON while
pipeline_state still reports DORMANT, the breaker would short-
circuit incorrectly, and real degradation goes silent.

This audit prevents that drift by:
  1. Greping `app/services/{adversarial_reviewer,sibling_hunt,
     iterative_fix}.py` (the 3 known brain enrichers) for their
     `os.getenv("..._ENABLED"...)` flags.
  2. Asserting each is present in
     `pipeline_state._BRAIN_ENRICHER_ENV_VARS`.
  3. If a developer adds a new enricher file in `app/services/`
     that follows the *_reviewer / *_hunt / *_fix / *_enricher /
     *_orchestrator naming convention AND gates on a
     `*_ENABLED` env var, this audit also flags it as a
     candidate to register.

Exit codes
----------
  0 — all known enrichers registered in pipeline_state
  1 — drift detected; commit blocked

Wired into preflight.sh + invariant_monitor (so a runtime --no-verify
bypass also catches the drift within 15 min of a brain-enricher
landing).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVICES = REPO / "app" / "services"
PIPELINE_STATE_PY = SERVICES / "pipeline_state.py"

# Known brain-enricher source files. Adding a 4th requires editing
# THIS audit (forces deliberate registration of the new env var).
KNOWN_ENRICHER_FILES: set[str] = {
    "adversarial_reviewer.py",
    "sibling_hunt.py",
    "iterative_fix.py",
}

# Regex for `os.getenv("FOO_ENABLED", ...)` and variants.
ENV_GETENV_RE = re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]*_ENABLED)["\']')

# Heuristic: any new file in app/services/ matching one of these
# naming conventions AND defining a `*_ENABLED` env var is a likely
# brain-enricher candidate. The audit warns (does NOT block) so the
# developer can either register or annotate as non-enricher.
ENRICHER_NAME_HINTS = re.compile(
    r"(_reviewer|_hunt|_fix|_enricher|_orchestrator|_brain)\.py$"
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _enricher_envs_in(path: Path) -> set[str]:
    return set(ENV_GETENV_RE.findall(_read(path)))


def main() -> int:
    if not PIPELINE_STATE_PY.is_file():
        print(
            f"❌ pipeline_state.py missing at {PIPELINE_STATE_PY} — "
            "either deleted or moved. Restore or update this audit."
        )
        return 1

    # Parse pipeline_state's registered set
    ps_src = _read(PIPELINE_STATE_PY)
    ps_match = re.search(
        r"_BRAIN_ENRICHER_ENV_VARS[^=]*=\s*\(([^)]+)\)",
        ps_src,
        re.DOTALL,
    )
    if not ps_match:
        print(
            "❌ Could not parse _BRAIN_ENRICHER_ENV_VARS in "
            "pipeline_state.py — refactor likely renamed the constant. "
            "Update this audit."
        )
        return 1
    registered = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', ps_match.group(1)))

    # Check 1: every known enricher file's env var is registered
    missing: dict[str, set[str]] = {}
    for fname in sorted(KNOWN_ENRICHER_FILES):
        f = SERVICES / fname
        if not f.is_file():
            print(
                f"⚠️  KNOWN_ENRICHER_FILES contains {fname} but file "
                "missing — refactor moved/deleted it; update this audit."
            )
            return 1
        envs = _enricher_envs_in(f)
        not_in_ps = envs - registered
        if not_in_ps:
            missing[fname] = not_in_ps

    # Check 2: heuristic warning for new enricher-shaped files
    suspect: dict[str, set[str]] = {}
    for f in SERVICES.glob("*.py"):
        if f.name in KNOWN_ENRICHER_FILES:
            continue
        if not ENRICHER_NAME_HINTS.search(f.name):
            continue
        envs = _enricher_envs_in(f)
        not_in_ps = envs - registered
        if not_in_ps:
            suspect[f.name] = not_in_ps

    if not missing and not suspect:
        print(
            f"✅ brain dormant-flag coverage clean — {len(registered)} "
            "enricher(s) registered, all known modules in sync."
        )
        return 0

    if missing:
        print("❌ FAIL — known brain enricher(s) NOT registered in "
              "pipeline_state._BRAIN_ENRICHER_ENV_VARS:")
        for f, envs in missing.items():
            for e in sorted(envs):
                print(f"   {f} → {e}")
        print()
        print("Fix: add the env var name to "
              "_BRAIN_ENRICHER_ENV_VARS in app/services/pipeline_state.py.")

    if suspect:
        prefix = "⚠️  " if not missing else ""
        print(f"{prefix}WARNING — file matching enricher-naming "
              "convention defines an *_ENABLED env var NOT in "
              "pipeline_state:")
        for f, envs in suspect.items():
            for e in sorted(envs):
                print(f"   {f} → {e}")
        print()
        print("If this is a brain enricher: add to KNOWN_ENRICHER_FILES "
              "in this audit AND to _BRAIN_ENRICHER_ENV_VARS in "
              "pipeline_state.")
        print("If NOT a brain enricher (e.g. a feature flag for a "
              "merchant-facing toggle): no action — this warning is "
              "informational.")

    # Suspect-only is informational; missing is hard fail.
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
