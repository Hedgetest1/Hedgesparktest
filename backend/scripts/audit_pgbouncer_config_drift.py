#!/usr/bin/env python
# invariant-eligible: true
#   Compares the version-controlled scripts/pgbouncer.ini against the
#   LIVE /etc/pgbouncer/pgbouncer.ini. Reflects runtime infra state, so
#   it is also meaningful for the 15-min invariant_monitor cycle (a
#   manual live edit that diverges from repo is a real regression).
"""audit_pgbouncer_config_drift.py — structural preventer (10k, ledger #14).

Born 2026-05-16. The c=768 0-error dashboard concurrency envelope
(proven 2026-05-15b) DEPENDS on PgBouncer `default_pool_size=80`,
`max_db_connections=150`, `max_client_conn=5000`. That tuning lived
ONLY at /etc/pgbouncer/pgbouncer.ini — scripts/pgbouncer.ini (whose
own deploy comment says `sudo cp` it to /etc) still carried the old
20 / (absent) / 200 defaults. A server rebuild from the repo would
have silently collapsed the entire 10k envelope back to the c≈64
pool-timeout cliff with zero signal.

This audit asserts the version-controlled mirror == the live config
for the params the 10k envelope + correctness depend on.

DEGRADE-OPEN: if the live /etc copy is absent or unreadable (fresh
dev box, CI, no pgbouncer, perm-restricted) the check SKIPS (exit 0)
— it can only FAIL when it can actually read both and they diverge.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_io import safe_read_text  # noqa: E402  TOCTOU-safe read

_REPO_INI = Path("/opt/wishspark/scripts/pgbouncer.ini")
_LIVE_INI = Path("/etc/pgbouncer/pgbouncer.ini")

# Params the 10k envelope / correctness depend on. Drift in any of
# these is a real regression; cosmetic params (log_*, listen_*) are
# intentionally not compared.
_KEYS = (
    "pool_mode",
    "default_pool_size",
    "max_db_connections",
    "max_client_conn",
    "reserve_pool_size",
    "server_idle_timeout",
    "ignore_startup_parameters",
)


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    txt = safe_read_text(path)
    if txt is None:  # absent / unreadable here — degrade-open
        return out
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#", "[")):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            if k in _KEYS:
                out[k] = v.strip()
    return out


def main() -> int:
    if not _REPO_INI.exists():
        print(f"audit_pgbouncer_config_drift: FAIL — repo mirror missing "
              f"({_REPO_INI}). The 10k envelope must be version-controlled.")
        return 1
    try:
        live = _parse(_LIVE_INI)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        print(f"audit_pgbouncer_config_drift: SKIP — live /etc copy "
              f"unreadable here ({type(exc).__name__}); cannot compare. "
              f"This is degrade-open by design (CI / fresh box / no "
              f"pgbouncer).")
        return 0
    if not live:
        print("audit_pgbouncer_config_drift: SKIP — live config parsed "
              "empty (not this host's pgbouncer).")
        return 0

    repo = _parse(_REPO_INI)
    drift: list[str] = []
    for k in _KEYS:
        rv, lv = repo.get(k), live.get(k)
        if rv != lv:
            drift.append(f"  {k}: repo={rv!r}  live={lv!r}")

    if drift:
        print("audit_pgbouncer_config_drift: FAIL — scripts/pgbouncer.ini "
              "has drifted from the live /etc tuned config (a rebuild "
              "would collapse the 10k envelope):")
        print("\n".join(drift))
        print("Fix: reconcile scripts/pgbouncer.ini with the live values "
              "(or, if the live edit was intentional, update the repo "
              "mirror in the same commit).")
        return 1
    print("audit_pgbouncer_config_drift: OK — repo mirror matches live "
          f"on all {len(_KEYS)} 10k-critical params.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
