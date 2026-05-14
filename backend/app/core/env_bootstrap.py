"""
Single, explicit environment bootstrap for the HedgeSpark backend.

This is the ONLY module in the codebase permitted to call load_dotenv().
Every process entry point (FastAPI app, PM2 workers, pytest conftest,
Alembic migrations, operator scripts) MUST call load_env() as the first
thing it does, before importing any module that reads os.getenv() at
import time.

Why a single bootstrap?
-----------------------
Previously load_dotenv() was scattered across application modules
(database, redis_client, shopify_auth, nudge_optimization_worker,
migrations, conftest, scripts). That created hidden import-time side
effects — whoever imported first "won" and loaded the env, and the
load order depended on import graph traversal rather than explicit
intent. This module replaces that pattern with one idempotent call
at each process boundary.

Contract
--------
* Idempotent: safe to call multiple times; only the first call reads
  the .env file.
* Absolute path: resolves backend/.env from __file__, so it works
  regardless of the process CWD.
* Non-overriding: load_dotenv() defaults to override=False, so any
  variable already set in the process environment (PM2 env injection,
  pytest overrides, CI secrets) wins over the .env file. This is
  required for tests that set APP_ENV=test before import.
"""
from __future__ import annotations

import logging
import stat
from pathlib import Path

from dotenv import load_dotenv

# backend/app/core/env_bootstrap.py -> backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"

_loaded = False
_log = logging.getLogger("wishspark.env_bootstrap")


def _audit_env_file_perms(env_file: Path) -> None:
    """Layer-2 perm check (static script is layer-1, invariant_monitor is
    layer-3). Logs CRITICAL on drift but does NOT crash — bricking
    production on a perm mistake is worse than the perm itself.

    Acceptable modes: 0o600 (owner rw) or 0o400 (owner read-only).
    Any group/world bit set (mode & 0o077) is a drift.
    """
    if not env_file.exists():
        return
    try:
        mode = stat.S_IMODE(env_file.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        _log.critical(
            "env_bootstrap: %s mode=%s is group/world-readable. "
            "Live secrets (API keys, encryption keys, OAuth) exposed. "
            "Run: chmod 600 %s",
            env_file, oct(mode), env_file,
        )


def load_env() -> None:
    """Load backend/.env into os.environ exactly once per process."""
    global _loaded
    if _loaded:
        return
    _audit_env_file_perms(_ENV_FILE)
    # override=False: existing environ values (set by PM2, pytest, CI) win.
    load_dotenv(_ENV_FILE, override=False)
    _loaded = True
