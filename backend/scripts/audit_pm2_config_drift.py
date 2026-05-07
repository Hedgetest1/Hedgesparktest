#!/usr/bin/env python3
"""audit_pm2_config_drift.py — fail when pm2-running args drift from
ecosystem.config.js.

Born 2026-05-07 after `wishspark-backend` was found running with
args `[uvicorn app.main:app --host 127.0.0.1 --port 8000]` while
ecosystem.config.js had `--workers 4`. Drift had been live for an
unknown period — only 1 worker reporting metrics instead of 4 → the
fleet_workers_reporting alert fired but only after manual investigation.

Behavior:
  - pm2 not installed             → exit 0 (skip; CI envs)
  - ecosystem.config.js missing   → exit 0 (skip)
  - parse failure (node/jlist)    → exit 0 with stderr warning
  - drift detected                → exit 1 (HARD FAIL)
  - clean                         → exit 0
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEM = REPO_ROOT / "ecosystem.config.js"


def _warn(msg: str) -> None:
    print(f"[audit_pm2_config_drift] WARN: {msg}", file=sys.stderr)


def _norm(args) -> list[str]:
    """Normalize args to token list. pm2 jlist returns a list;
    ecosystem.config.js holds a string — shlex-split for comparison."""
    if args is None:
        return []
    if isinstance(args, list):
        return [str(a) for a in args]
    return shlex.split(str(args))


def _load_ecosystem() -> dict[str, list[str]] | None:
    if not ECOSYSTEM.exists():
        _warn(f"{ECOSYSTEM} missing — skip")
        return None
    if shutil.which("node") is None:
        _warn("node not installed — cannot parse ecosystem.config.js, skip")
        return None
    try:
        out = subprocess.check_output(
            [
                "node", "-e",
                f"console.log(JSON.stringify(require({json.dumps(str(ECOSYSTEM))})))",
            ],
            stderr=subprocess.PIPE,
            timeout=10,
        )
        data = json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        _warn(f"could not parse ecosystem.config.js: {exc!r} — skip")
        return None
    return {a["name"]: _norm(a.get("args")) for a in data.get("apps", [])}


def _load_pm2() -> dict[str, list[str]] | None:
    if shutil.which("pm2") is None:
        _warn("pm2 not installed — skip")
        return None
    try:
        out = subprocess.check_output(
            ["pm2", "jlist"], stderr=subprocess.PIPE, timeout=10,
        )
        data = json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        _warn(f"pm2 jlist failed: {exc!r} — skip")
        return None
    result: dict[str, list[str]] = {}
    for proc in data:
        name = proc.get("name")
        if not name:
            continue
        args = proc.get("pm2_env", {}).get("args") or proc.get("args")
        result[name] = _norm(args)
    return result


def main() -> int:
    cfg = _load_ecosystem()
    live = _load_pm2()
    if cfg is None or live is None:
        return 0  # skip path

    drifts: list[str] = []
    for name, cfg_args in cfg.items():
        if name not in live:
            # not running — separate concern (process down); not args-drift
            continue
        live_args = live[name]
        if cfg_args != live_args:
            drifts.append(
                f"  {name}\n    config: {cfg_args}\n    live  : {live_args}"
            )

    if drifts:
        print("PM2 CONFIG DRIFT DETECTED — live args differ from ecosystem.config.js:")
        print("\n".join(drifts))
        print(
            "\nFix: `pm2 delete <name> && pm2 start ecosystem.config.js "
            "--only <name> && pm2 save`"
        )
        return 1

    print(f"OK: pm2 args match ecosystem.config.js for {len(cfg)} app(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
