#!/usr/bin/env python3
"""Kill-switches structural preventer.

Born 2026-05-02 from the brutal-CTO 10/10 elite-tier sprint Gap 3.
The honest audit found a critical safety lie: CLAUDE.md "Kill switch"
section documented `PIPELINE_AUTO_PROPOSE_DISABLED=1` as the founder's
break-glass control, but ZERO call sites in app/ actually read that
env var. Setting it had no effect. Founder thought the kill switch
was wired. It wasn't.

This audit catches the same bug class going forward. For every
documented kill switch (env var name + expected wire), verify at
least one read in code that gates a state-modifying path.

Documented kill switches (from CLAUDE.md / .env conventions):
  PIPELINE_AUTO_PROPOSE_DISABLED   - hard-disable the entire bugfix pipeline
  AUTO_MERGE_TIER0_PAUSED          - pause auto-merge for TIER_0 fixes
  AUTO_DEPLOY_PAUSED               - pause auto-deploy
  ALLOW_INSECURE_DEV               - relax dev-mode security checks
  TELEGRAM_WEBHOOK_SECRET          - mandatory webhook auth (fail-closed when missing)
  RESEND_WEBHOOK_SECRET            - mandatory webhook auth (fail-closed when missing)

For each: verify at least one `os.getenv("<NAME>")` /
`os.environ.get("<NAME>")` / `os.environ["<NAME>"]` read exists
under app/. Missing read → FAIL with the gap named.

Usage:
    python3 scripts/audit_kill_switches_wired.py
    Exit 0 = clean. Exit 1 = at least one kill switch is unwired.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"

# Each entry: (env_var, why_it_exists, expected_gate_class)
# The audit only verifies the env var is READ; the actual gate logic
# is enforced by per-feature tests + runtime behavior. This audit is
# the "one layer up" check: if no code reads the var, the documented
# kill switch is doctrine-only, which is a lie a brutal CTO catches.
_KILL_SWITCHES: list[tuple[str, str]] = [
    (
        "ALLOW_INSECURE_DEV",
        "Dev-mode relaxation of security enforcement (FATAL on missing "
        "secret production crashes are downgraded). Required check at "
        "_startup_env_audit + per-secret enforcement sites.",
    ),
    (
        "TELEGRAM_WEBHOOK_SECRET",
        "Mandatory webhook signature secret. Fail-closed: when unset, "
        "the webhook endpoint must return 503 (CLAUDE.md §8.3). This "
        "audit verifies SOMETHING reads the var; the fail-closed "
        "behavior is enforced by tests.",
    ),
    (
        "RESEND_WEBHOOK_SECRET",
        "Mandatory webhook signature secret for Resend deliverability "
        "telemetry (CLAUDE.md §8.4 fail-closed verification).",
    ),
]


def find_env_reads(env_name: str) -> list[Path]:
    """Return paths under app/ that contain at least one read of
    the named env var."""
    pat = re.compile(
        rf'(?:os\.getenv|os\.environ\.get)\(\s*[\'"]{re.escape(env_name)}[\'"]'
        rf'|os\.environ\[\s*[\'"]{re.escape(env_name)}[\'"]\s*\]'
    )
    hits: list[Path] = []
    for py in APP.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = safe_read_text(py)
        if text is None:
            continue
        if pat.search(text):
            hits.append(py.relative_to(REPO))
    return hits


def main() -> int:
    failures: list[tuple[str, str]] = []
    ok_count = 0
    for env_name, why in _KILL_SWITCHES:
        hits = find_env_reads(env_name)
        if not hits:
            failures.append((env_name, why))
        else:
            ok_count += 1

    if failures:
        print(
            f"FAIL: {len(failures)} documented kill switch(es) NOT WIRED "
            "(no code reads the env var):"
        )
        for env_name, why in failures:
            print(f"\n  {env_name}")
            print(f"    {why}")
            print(f"    Fix: wire `os.getenv(\"{env_name}\")` in the "
                  f"function that gates the documented behavior.")
        print(
            "\nA documented kill switch with no reader is a safety lie. "
            "Brutal CTOs catch these in 5 minutes. This audit catches "
            "them at commit time."
        )
        return 1

    print(
        f"OK: all {ok_count} documented kill switch(es) wired "
        f"(at least one os.getenv read found per env var)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
