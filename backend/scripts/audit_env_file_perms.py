#!/usr/bin/env python
# invariant-eligible: false
# Reason: the in-process runtime equivalent already lives at
# `app/services/invariant_monitor.py::_check_env_file_perms` (wired
# into the 15-min cycle, writes invariant:env_perm_drift alert,
# auto-heals on chmod 600). This script is layer-1 (preflight + CI)
# of the 3-layer defense; running it again from invariant_monitor
# as a subprocess would duplicate work without adding coverage.
"""
audit_env_file_perms.py — enforce 600 / 400 perms on every .env file on disk.

Born 2026-05-14 after an external CTO audit flagged
`/opt/wishspark/backend/.env` with mode 644 (world-readable). The file
holds live Shopify API secret, encryption keys, Telegram bot token,
Resend / Anthropic / OpenAI keys, Sentry webhook secret. Any process
running on the VPS could read it.

Three-layer defense (this is layer 1: static / preflight):
  1. STATIC (this script) — pre-commit / CI sweep. Hard-fail.
  2. STARTUP (app/core/env_bootstrap.py::load_env) — log CRITICAL on
     drift; non-blocking (we don't brick production on a perm mistake).
  3. RUNTIME (app/services/invariant_monitor._check_env_file_perms) —
     15-min cycle. Writes `env_perm_drift` alert. Auto-heals when
     perms restored.

Why all three: the static script doesn't run on a deployed box if
preflight is skipped. The startup check fires only at boot. The
invariant runs continuously. Defense-in-depth so a single failure
mode (forgotten chmod after edit) is caught somewhere.

Exit codes:
    0 — all env files are 600 or 400 (read-only owner)
    1 — drift detected, world/group readable env file on disk
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Files that hold live secrets and MUST be 600/400. Gitignored, sit on disk.
_ENV_FILES: tuple[Path, ...] = (
    _REPO_ROOT / "backend" / ".env",
    _REPO_ROOT / "dashboard" / ".env.local",
)

# Maximum permitted permission bits. 0o600 = owner rw, nothing else.
# 0o400 (owner read-only) also accepted — stricter, fine.
_MAX_MODE = 0o600


def check_perms() -> list[tuple[Path, int]]:
    """Returns list of (path, mode) for files that violate the perm policy.
    Missing files are skipped (a dev box without the file is not a drift —
    the env_bootstrap will fail elsewhere)."""
    violations: list[tuple[Path, int]] = []
    for p in _ENV_FILES:
        if not p.exists():
            continue
        mode = stat.S_IMODE(p.stat().st_mode)
        # group or world readable/writable/executable → violation
        if mode & 0o077:
            violations.append((p, mode))
    return violations


def main() -> int:
    violations = check_perms()
    if not violations:
        print(
            f"audit_env_file_perms: clean — {len(_ENV_FILES)} env file(s) "
            f"checked, all 600 or 400"
        )
        return 0
    print("audit_env_file_perms: VIOLATIONS DETECTED")
    for path, mode in violations:
        print(f"  {path}: mode={oct(mode)} (must be 0o600 or 0o400)")
    print()
    print("Fix:")
    for path, _ in violations:
        print(f"  chmod 600 {path}")
    print()
    print(
        "Why this matters: env files hold live API keys, encryption "
        "secrets, OAuth credentials. World/group-readable means any "
        "process or user on the host can exfiltrate them."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
