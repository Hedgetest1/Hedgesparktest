#!/usr/bin/env python3
"""DB-pool config doctrine preventer.

Born 2026-05-02 from the brutal-CTO post-elite-tier inspection. Found:
  - app/core/database.py defaulted POOL_SIZE=20, MAX_OVERFLOW=40
    (single-worker numbers, never updated when runtime flipped to
    uvicorn --workers 4 in late April).
  - 4 backend workers × (20 + 40) = 240 connections, Postgres
    max_connections=200 → ceiling exceeded by 20 %, with 20+ live
    QueuePool exhaustion errors in the production error log.
  - CLAUDE.md §6 documents the correct math (5 + 10 per worker
    → 84 conn total against the 200 cap) but the code drifted.

This audit catches the same drift class going forward. For each
of the 3 sources of truth, extract the configured pool numbers
and verify they agree:

  1. app/core/database.py defaults  — POOL_SIZE / POOL_MAX_OVERFLOW
  2. backend/.env                   — DB_POOL_SIZE / DB_MAX_OVERFLOW
                                      (optional override; checked when set)
  3. CLAUDE.md §6 doctrine string   — "(5 + 10)" or similar

Plus the worker-count math:
  ecosystem.config.js               — uvicorn --workers <N>
And the Postgres ceiling:
  CLAUDE.md doctrine                — max_connections >= 200

Failure modes detected:
  - code default × workers > Postgres max_connections (drift catastrophe)
  - .env override silently raises without doctrine update
  - CLAUDE.md doctrine vs code default disagreement

Usage:
    python3 scripts/audit_db_pool_doctrine.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
DATABASE_PY = REPO / "app" / "core" / "database.py"
ECOSYSTEM = REPO.parent / "ecosystem.config.js"
CLAUDE_MD = REPO.parent / "CLAUDE.md"
ENV_FILE = REPO / ".env"

# Postgres max_connections invariant — CLAUDE.md §6 says 200. We tolerate
# higher (some operators provision more) but never lower.
_POSTGRES_MAX_CONNECTIONS_FLOOR = 200

# PgBouncer awareness — born 2026-05-04 (10k-readiness sprint). When
# DATABASE_URL points at port 6432 (PgBouncer), the relevant ceiling
# is PgBouncer's max_client_conn (default 5000), NOT Postgres
# max_connections. PgBouncer multiplexes app conns onto a smaller
# server-side PG pool (max_db_connections, default 100).
_PGBOUNCER_PORT = 6432
_PGBOUNCER_MAX_CLIENT_CONN_FLOOR = 5000


def _detect_pgbouncer(env_text: str) -> bool:
    """Return True if DATABASE_URL points at PgBouncer (port 6432)."""
    for line in env_text.splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1]
            if f":{_PGBOUNCER_PORT}/" in value:
                return True
            return False
    return False


def _read(path: Path) -> str:
    return safe_read_text(path) or ""


def _extract_db_pool_defaults(text: str) -> tuple[int | None, int | None]:
    """Parse `POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "<N>"))` and
    `POOL_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "<N>"))`."""
    pool_size = None
    max_overflow = None
    m = re.search(
        r'POOL_SIZE\s*=\s*int\(\s*os\.getenv\(\s*"DB_POOL_SIZE"\s*,\s*"(\d+)"',
        text,
    )
    if m:
        pool_size = int(m.group(1))
    m = re.search(
        r'POOL_MAX_OVERFLOW\s*=\s*int\(\s*os\.getenv\(\s*"DB_MAX_OVERFLOW"\s*,\s*"(\d+)"',
        text,
    )
    if m:
        max_overflow = int(m.group(1))
    return pool_size, max_overflow


def _extract_env_overrides(text: str) -> tuple[int | None, int | None]:
    """Parse DB_POOL_SIZE=N and DB_MAX_OVERFLOW=N from .env."""
    pool_size = None
    max_overflow = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DB_POOL_SIZE="):
            try:
                pool_size = int(line.split("=", 1)[1].split("#")[0].strip())
            except Exception:
                pass
        elif line.startswith("DB_MAX_OVERFLOW="):
            try:
                max_overflow = int(line.split("=", 1)[1].split("#")[0].strip())
            except Exception:
                pass
    return pool_size, max_overflow


def _extract_uvicorn_workers(text: str) -> int | None:
    """Parse `--workers N` from ecosystem.config.js."""
    m = re.search(r"--workers\s+(\d+)", text)
    return int(m.group(1)) if m else None


def _extract_doctrine_pool(text: str) -> tuple[int | None, int | None]:
    """Parse the CLAUDE.md §6 line that documents the pool math.
    Looks for `(5 + 10)` style. Returns (size, overflow)."""
    m = re.search(r"\(\s*(\d+)\s*\+\s*(\d+)\s*\)\s*=\s*\d+\s*conn", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def main() -> int:
    failures: list[str] = []

    db_size, db_overflow = _extract_db_pool_defaults(_read(DATABASE_PY))
    env_size, env_overflow = _extract_env_overrides(_read(ENV_FILE))
    workers = _extract_uvicorn_workers(_read(ECOSYSTEM))
    doc_size, doc_overflow = _extract_doctrine_pool(_read(CLAUDE_MD))

    if db_size is None or db_overflow is None:
        failures.append(
            "could not parse POOL_SIZE / POOL_MAX_OVERFLOW from "
            "app/core/database.py — has the variable been renamed?"
        )

    # Effective values: env overrides code defaults
    eff_size = env_size if env_size is not None else db_size
    eff_overflow = env_overflow if env_overflow is not None else db_overflow

    if eff_size is None or eff_overflow is None or workers is None:
        failures.append(
            "could not derive effective worker × pool math — at least "
            "one of size/overflow/workers missing."
        )
    else:
        ceiling = workers * (eff_size + eff_overflow)
        # Add 14 for PM2 singleton workers and ~10 admin headroom.
        ceiling_total = ceiling + 14 + 10
        # PgBouncer changes the math: the app-side pool talks to
        # PgBouncer (port 6432), which multiplexes onto a smaller
        # server-side PG pool. So the relevant ceiling is PgBouncer's
        # max_client_conn (5000 in our config), NOT PG max_connections.
        using_pgbouncer = _detect_pgbouncer(_read(ENV_FILE))
        if using_pgbouncer:
            if ceiling_total > _PGBOUNCER_MAX_CLIENT_CONN_FLOOR:
                failures.append(
                    f"DB pool math exceeds PgBouncer max_client_conn: "
                    f"{workers} uvicorn workers × ({eff_size}+{eff_overflow}) "
                    f"= {ceiling} client conn, +14 PM2 +10 admin = "
                    f"{ceiling_total} > PgBouncer max_client_conn floor "
                    f"({_PGBOUNCER_MAX_CLIENT_CONN_FLOOR}). Either lower "
                    f"DB pool OR raise pgbouncer.ini max_client_conn."
                )
        else:
            if ceiling_total > _POSTGRES_MAX_CONNECTIONS_FLOOR:
                failures.append(
                    f"DB pool math exceeds Postgres invariant: "
                    f"{workers} uvicorn workers × ({eff_size}+{eff_overflow}) "
                    f"= {ceiling} backend conn, +14 PM2 +10 admin = "
                    f"{ceiling_total} > Postgres max_connections invariant "
                    f"floor ({_POSTGRES_MAX_CONNECTIONS_FLOOR}). Either lower "
                    f"DB_POOL_SIZE / DB_MAX_OVERFLOW OR raise Postgres "
                    f"max_connections + update doctrine. "
                    f"NOTE: install PgBouncer (transaction pool) and route "
                    f"DATABASE_URL to port 6432 to lift this ceiling."
                )

    # Doctrine drift — code defaults must match CLAUDE.md §6 doctrine
    if doc_size is not None and db_size is not None and db_size != doc_size:
        failures.append(
            f"code default POOL_SIZE={db_size} but CLAUDE.md §6 "
            f"doctrine documents pool_size={doc_size} — update one or "
            f"the other to keep them aligned."
        )
    if (
        doc_overflow is not None
        and db_overflow is not None
        and db_overflow != doc_overflow
    ):
        failures.append(
            f"code default MAX_OVERFLOW={db_overflow} but CLAUDE.md §6 "
            f"doctrine documents max_overflow={doc_overflow} — update "
            f"one or the other."
        )

    if failures:
        print(
            f"FAIL: {len(failures)} DB-pool doctrine finding(s):"
        )
        for f in failures:
            print(f"  - {f}")
        print(
            "\nThis audit catches the 2026-05-02 drift class — "
            "single-worker pool defaults left in place after the "
            "uvicorn --workers 4 flip → 20× QueuePool exhaustions "
            "in production. The docstring/doctrine and the code MUST "
            "agree at all times."
        )
        return 1

    if using_pgbouncer:
        print(
            f"OK: DB pool math green — {workers}×({eff_size}+{eff_overflow})"
            f"+14+10={workers*(eff_size+eff_overflow)+24} ≤ "
            f"PgBouncer max_client_conn floor "
            f"{_PGBOUNCER_MAX_CLIENT_CONN_FLOOR} (PgBouncer in front; "
            f"PG max_connections={_POSTGRES_MAX_CONNECTIONS_FLOOR} "
            f"protected by PgBouncer max_db_connections=100)."
        )
    else:
        print(
            f"OK: DB pool math green — {workers}×({eff_size}+{eff_overflow})"
            f"+14+10={workers*(eff_size+eff_overflow)+24} ≤ "
            f"Postgres max_connections floor {_POSTGRES_MAX_CONNECTIONS_FLOOR}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
