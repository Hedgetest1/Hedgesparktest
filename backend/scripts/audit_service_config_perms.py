#!/usr/bin/env python
# invariant-eligible: false
# Reason: the in-process runtime equivalent lives at
# `app/services/invariant_monitor.py::_check_service_config_perms`
# (wired into the 15-min cycle, writes invariant:service_config_perm_drift
# alert, auto-resolves when ownership/mode restored). This script is
# layer-1 (preflight + on-demand) of the 2-layer defense; re-running it
# from invariant_monitor as a subprocess would duplicate without adding
# coverage.
"""
audit_service_config_perms.py — enforce service-user ownership + non-
world-readable mode on system config files owned by a privilege-
dropping daemon.

Born 2026-05-18 — mechanizes `feedback_root_edit_breaks_service_config_
perms.md` (the long-carried #13 R-fix, doctrine-only across ≥4
sessions). The 2026-05-15b incident: editing `/etc/pgbouncer/
pgbouncer.ini` with root tooling (Edit/Write) rewrote it `root:root`
640 (was `postgres:postgres` 640). RELOAD still worked (old process
had the fd open) but `systemctl restart pgbouncer` then FAILED —
`could not load file "...": Permission denied` — because pgbouncer
drops privileges to `postgres`, which could no longer read the
root-owned file. The failed restart killed the working pgbouncer →
backend 503 (DATABASE_URL → :6432) for ~3 min until perms were
restored. There was NO detection until the restart failed: this
preventer closes exactly that detection gap.

Two failure modes detected:
  1. **Owner/group flip** (the actual incident) — the privilege-
     dropped daemon can no longer read its own config; the NEXT
     restart is a self-inflicted outage waiting to happen.
  2. **World-readable mode** — `userlist.txt` carries md5 password
     hashes; world-readable means any process/user on the host can
     read DB credentials.

This is the analogue of `audit_env_file_perms.py` but adapted: for a
`.env` the dominant risk is mode-opening; for a service config owned
by a privilege-dropping daemon the dominant risk is OWNER flip
(invisible until the next restart). 2-layer defense (NOT 3 — there is
no env_bootstrap-style boot reader for pgbouncer.ini in our Python;
fabricating a 3rd layer would be theater):
  1. STATIC (this script) — preflight + on-demand. Hard-fail.
  2. RUNTIME (invariant_monitor._check_service_config_perms) — 15-min
     cycle. Writes `service_config_perm_drift` CRITICAL alert,
     auto-resolves when ownership/mode restored. Catches drift
     introduced AFTER a commit (a root Edit of /etc/* between
     commits), within minutes — BEFORE the next restart.

Expected values are ground-truthed from the live host (2026-05-18:
`stat -c '%U:%G %a'` → both `postgres:postgres 640`), not assumed.
Missing files are skipped (a host without pgbouncer at that path is
not a drift — the manifest is best-effort, fail-open on absence).

Exit codes:
    0 — every present service config has the expected owner/group and
        is not world-accessible and owner-readable
    1 — drift detected (owner/group flip OR world-readable OR owner
        cannot read)
"""
from __future__ import annotations

import grp
import os
import pwd
import stat
import sys

# Manifest: path -> (expected_owner, expected_group). Mode policy is
# uniform (owner must be able to read; nothing world-accessible) so it
# is not per-entry. Ground-truthed live 2026-05-18.
_SERVICE_CONFIGS: tuple[tuple[str, str, str], ...] = (
    ("/etc/pgbouncer/pgbouncer.ini", "postgres", "postgres"),
    ("/etc/pgbouncer/userlist.txt", "postgres", "postgres"),
)


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return f"gid:{gid}"


def evaluate(
    expected_owner: str,
    expected_group: str,
    actual_owner: str,
    actual_group: str,
    mode: int,
) -> str | None:
    """Pure policy predicate (no filesystem) — returns a drift reason
    string, or None if compliant. Unit-testable without root/chown.

    Drift iff ANY of:
      - owner != expected (the daemon can't read its own config →
        next restart is a self-inflicted outage)
      - group != expected
      - world-accessible (`mode & 0o007`) — credential exposure
      - owner cannot read (`not mode & 0o400`) — also breaks the
        daemon
    """
    reasons: list[str] = []
    if actual_owner != expected_owner:
        reasons.append(
            f"owner={actual_owner} (expected {expected_owner} — a "
            f"privilege-dropped daemon cannot read a {actual_owner}-owned "
            f"config; the next restart fails → self-inflicted outage)"
        )
    if actual_group != expected_group:
        reasons.append(f"group={actual_group} (expected {expected_group})")
    if mode & 0o007:
        reasons.append(
            f"mode={oct(mode)} is world-accessible (credential exposure)"
        )
    if not (mode & 0o400):
        reasons.append(
            f"mode={oct(mode)} — owner has no read bit (daemon cannot "
            f"load its config)"
        )
    return "; ".join(reasons) if reasons else None


def check() -> list[tuple[str, str]]:
    """Returns [(path, reason)] for every present, drifted service
    config. Missing files skipped (fail-open on absence)."""
    out: list[tuple[str, str]] = []
    for path, exp_owner, exp_group in _SERVICE_CONFIGS:
        try:
            st = os.stat(path)
        except OSError:
            continue  # absent / unreadable host path — not a drift
        reason = evaluate(
            exp_owner,
            exp_group,
            _owner_name(st.st_uid),
            _group_name(st.st_gid),
            stat.S_IMODE(st.st_mode),
        )
        if reason:
            out.append((path, reason))
    return out


def main() -> int:
    drift = check()
    if not drift:
        print(
            f"audit_service_config_perms: clean — "
            f"{len(_SERVICE_CONFIGS)} service config(s) checked, "
            f"correct owner/group + not world-accessible"
        )
        return 0
    print("audit_service_config_perms: DRIFT DETECTED")
    for path, reason in drift:
        print(f"  {path}: {reason}")
    print()
    print("Fix (restore the service user as owner, non-world mode):")
    for path, _ in drift:
        print(f"  chown postgres:postgres {path} && chmod 640 {path}")
    print()
    print(
        "Why this matters: editing a service-owned /etc config with "
        "root tooling rewrites it root:root. RELOAD keeps working (the "
        "running process holds the fd) so the drift is INVISIBLE until "
        "the next restart, which then fails and takes the dependency "
        "(pgbouncer → the whole backend) down. "
        "feedback_root_edit_breaks_service_config_perms.md."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
