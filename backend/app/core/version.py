"""
version.py — captures the git SHA of the running process at import time.

This solves a specific deploy-observability gap surfaced on 2026-04-11:
the backend had been running for 11 hours while `.next/` on disk and
the disk-resident Python code had been updated multiple times. `pm2 list`
showed "online" but the process was serving the old codebase — and
there was NO way to prove it from the outside without manually
`pm2 restart`ing and watching the logs.

By reading `git rev-parse HEAD` ONCE at module import and caching it
in a module-level constant, the answer we expose from /system/health
is **immutably the SHA the process was launched on**. Every PM2 restart
re-imports this module and captures a fresh value. If the reported
SHA doesn't match the current on-disk SHA, the deploy never took
effect — and deploy_gate.py can detect that unambiguously.

Safe by construction:
- No runtime git call: just one subprocess at module import.
- No env var dependency: works identically in dev, CI, prod, container.
- Silent fallback: if git is unavailable, we report "unknown" instead
  of crashing the import.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_REPO_DIR = "/opt/wishspark/backend"


def _capture_git_sha() -> str:
    """Run `git rev-parse HEAD` once at import. Return the short SHA or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_DIR,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        log.debug("version: git rev-parse failed: %s", exc)
    return "unknown"


def _capture_git_describe() -> str:
    """`git describe --always --dirty` — gives a human-readable tag if available."""
    try:
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--tags"],
            cwd=_REPO_DIR,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        log.warning("version: git describe failed: %s", exc)
    return "unknown"


# Module-level — captured ONCE at first import (process startup).
GIT_SHA: str = _capture_git_sha()
GIT_DESCRIBE: str = _capture_git_describe()
PROCESS_STARTED_AT: str = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
PROCESS_PID: int = os.getpid()


def get_version_info() -> dict:
    """
    Return the immutable version snapshot captured at process startup.

    Consumers:
      * /system/health — embeds this in the report so operators and
        deploy_gate.py can verify that a pm2 restart actually took
        effect.
      * /ops/pipeline-health — same.
      * deploy_gate.py postdeploy — compares the reported SHA to the
        on-disk SHA. If they differ, the restart was a no-op.
    """
    return {
        "git_sha": GIT_SHA,
        "git_sha_short": GIT_SHA[:12] if GIT_SHA != "unknown" else "unknown",
        "git_describe": GIT_DESCRIBE,
        "process_started_at": PROCESS_STARTED_AT,
        "pid": PROCESS_PID,
    }
