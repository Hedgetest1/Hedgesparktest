#!/usr/bin/env python3
"""Log-rotation health preventer.

Born 2026-05-02 from the brutal-CTO post-elite-tier inspection.
Found:
  - /root/.pm2/logs/wishspark-backend-error.log = 104 MB, unrotated
    since 2026-03-27 (~5 weeks of accumulation).
  - pm2-logrotate module was NEVER installed.
  - No /etc/logrotate.d entry covered PM2 logs.
  - Disk OK at 17/96 GB now, but trajectory was unbounded.

Fix shipped 2026-05-02:
  - `pm2 install pm2-logrotate` (v3.0.0).
  - max_size=50M, retain=7, compress=true, daily rotateInterval.
  - The 104 MB log rotated + gzip-archived; backend reloaded.

This audit catches REGRESSION of any of the above:
  1. pm2-logrotate process must be online.
  2. Configured max_size must be <= 100 MB.
  3. No PM2 log file may exceed 200 MB on disk (hard alert).
  4. /root/.pm2/logs disk usage must be <= 5 GB.

Usage:
    python3 scripts/audit_log_rotation_health.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PM2_LOGS_DIR = Path("/root/.pm2/logs")
_HARD_FILE_LIMIT_BYTES = 200 * 1024 * 1024  # 200 MB
_HARD_DIR_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
_MAX_SIZE_CONFIG_CEILING_MB = 100  # configured max_size must be <= 100 M


def _pm2_module_status(name: str) -> dict | None:
    """Parse `pm2 ls` output for the named module (the unified list
    contains both apps + modules). Older `pm2 module:list` doesn't
    exist on PM2 5.x. Returns dict {status} or None."""
    try:
        out = subprocess.check_output(
            ["pm2", "ls"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return None
    for line in out.splitlines():
        if name not in line:
            continue
        low = line.lower()
        if "online" in low:
            return {"status": "online"}
        if "stopped" in low or "errored" in low:
            return {"status": "stopped"}
        return {"status": "unknown"}
    return None


def _pm2_module_config(name: str) -> dict[str, str]:
    """Parse `pm2 conf <module>` output into a dict."""
    try:
        out = subprocess.check_output(
            ["pm2", "conf", name],
            stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return {}
    cfg: dict[str, str] = {}
    for line in out.splitlines():
        # Expect lines like: `$ pm2 set pm2-logrotate:max_size 50M`
        m = re.match(rf"\$\s+pm2\s+set\s+{re.escape(name)}:(\S+)\s+(.+)$", line.strip())
        if m:
            cfg[m.group(1)] = m.group(2).strip()
    return cfg


def _parse_size_bytes(spec: str) -> int | None:
    """Parse '50M' / '500K' / '2G' to bytes."""
    spec = spec.strip().upper()
    m = re.match(r"^(\d+)\s*([KMG]?)B?$", spec)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    factor = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}.get(unit, 1)
    return n * factor


def _largest_log_file() -> tuple[Path | None, int]:
    if not PM2_LOGS_DIR.is_dir():
        return None, 0
    biggest = None
    biggest_size = 0
    for f in PM2_LOGS_DIR.iterdir():
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except Exception:
            continue
        if size > biggest_size:
            biggest_size = size
            biggest = f
    return biggest, biggest_size


def _dir_size_bytes(p: Path) -> int:
    if not p.is_dir():
        return 0
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except Exception:
            continue
    return total


def main() -> int:
    failures: list[str] = []

    # 1. pm2-logrotate process must be online
    status = _pm2_module_status("pm2-logrotate")
    if status is None:
        failures.append(
            "pm2-logrotate module is NOT installed. "
            "Run: pm2 install pm2-logrotate"
        )
    elif status.get("status") != "online":
        failures.append(
            f"pm2-logrotate module status is '{status.get('status')}'. "
            "Run: pm2 restart pm2-logrotate"
        )

    # 2. configured max_size must be <= ceiling
    cfg = _pm2_module_config("pm2-logrotate")
    raw_size = cfg.get("max_size", "")
    cfg_bytes = _parse_size_bytes(raw_size) if raw_size else None
    if cfg_bytes is None:
        failures.append(
            "pm2-logrotate max_size is unconfigured (or unparseable). "
            f"Run: pm2 set pm2-logrotate:max_size {_MAX_SIZE_CONFIG_CEILING_MB}M"
        )
    elif cfg_bytes > _MAX_SIZE_CONFIG_CEILING_MB * 1024 * 1024:
        failures.append(
            f"pm2-logrotate max_size={raw_size} exceeds the "
            f"{_MAX_SIZE_CONFIG_CEILING_MB} MB ceiling — "
            "lowers the rotation safety margin."
        )

    # 3. no log file > hard limit
    biggest, big_bytes = _largest_log_file()
    if biggest is not None and big_bytes > _HARD_FILE_LIMIT_BYTES:
        mb = big_bytes // (1024 ** 2)
        failures.append(
            f"log file exceeds hard limit: {biggest.name} = {mb} MB "
            f"(limit {_HARD_FILE_LIMIT_BYTES // (1024 ** 2)} MB). "
            "Either rotate manually or investigate why pm2-logrotate "
            "did not catch it."
        )

    # 4. log dir total <= 5 GB
    dir_bytes = _dir_size_bytes(PM2_LOGS_DIR)
    if dir_bytes > _HARD_DIR_LIMIT_BYTES:
        gb = dir_bytes // (1024 ** 3)
        failures.append(
            f"PM2 log dir total {gb} GB exceeds 5 GB ceiling — "
            "increase rotation aggressiveness or archive old logs."
        )

    if failures:
        print(f"FAIL: {len(failures)} log-rotation finding(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    big_mb = big_bytes // (1024 ** 2)
    dir_mb = dir_bytes // (1024 ** 2)
    print(
        f"OK: pm2-logrotate online, max_size={raw_size}, "
        f"largest log {big_mb} MB, dir total {dir_mb} MB."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
