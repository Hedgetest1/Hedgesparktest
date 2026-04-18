"""
audit_dashboard_live.py — Structural preventer for stale Next.js
in-memory manifests serving 5xx on referenced chunks.

Background
----------
On 2026-04-18 the founder reported the landing rendering as unstyled
copy on white. Investigation:

  - `wishspark-dashboard` PM2 process uptime: 15h.
  - `.next/BUILD_ID` mtime: 4h old.
  - Landing HTML referenced `/_next/static/chunks/016v8mizti10q.css`
    which returned **500** because that chunk no longer existed on disk.

Root cause: `npx next build` was run mid-process-lifetime. Next.js /
Turbopack reads chunk manifests at process startup into RAM. When the
on-disk chunks were rewritten, the running process kept generating HTML
pointing at the OLD hashes — some of which were deleted during rebuild.

The bug was invisible to `curl /` (HTTP 200 still served the HTML
envelope), invisible to `pm2 list` (process was "online"), invisible
to Lighthouse/a11y/smoke tests (none of which fetch the CSS chunks).
Merchants just saw a broken white page.

What this script does
---------------------
Two-layer check:

  1. **Freshness**: if `.next/BUILD_ID` mtime > dashboard PM2 process
     start time, fail loudly — rebuild without restart is a latent
     bug waiting to break.

  2. **Asset resolution**: fetch dashboard `/`, extract every
     `/_next/static/chunks/*.{css,js}` and `/_next/static/media/*`
     reference, verify each returns 200. Any 404/5xx fails.

Skips cleanly when dashboard is unreachable at 127.0.0.1:3000 so
backend-only commits don't force the dashboard to be up.

Usage
-----
  ./venv/bin/python scripts/audit_dashboard_live.py              # report
  ./venv/bin/python scripts/audit_dashboard_live.py --strict     # exit 1 on any issue

Scope is narrowly the dashboard's own assets — the exact bug class we
saw. Small, targeted, fast (<2 s on a warm cache).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Tuple

DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "http://127.0.0.1:3000")
DASHBOARD_DIR = Path(os.environ.get("DASHBOARD_DIR", "/opt/wishspark/dashboard"))
PROBE_PATHS = ["/", "/app", "/pricing"]
TIMEOUT_S = 5
ASSET_RE = re.compile(r'/_next/static/(?:chunks|media)/[A-Za-z0-9_~.\-]+\.[A-Za-z0-9]+')


def _fetch(url: str) -> Tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def _dashboard_reachable() -> bool:
    code, _ = _fetch(f"{DASHBOARD_HOST}/")
    return 200 <= code < 400


def _build_id_mtime() -> float | None:
    bid = DASHBOARD_DIR / ".next" / "BUILD_ID"
    if not bid.exists():
        return None
    return bid.stat().st_mtime


def _pm2_dashboard_start() -> float | None:
    """Return dashboard PM2 process start time as epoch seconds, or None if
    pm2 is unavailable or the app is not registered."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["pm2", "jlist"], stderr=subprocess.DEVNULL, timeout=5
        )
        entries = json.loads(out.decode("utf-8", "replace"))
    except Exception:
        return None
    for e in entries:
        if e.get("name") == "wishspark-dashboard":
            ts_ms = e.get("pm2_env", {}).get("pm_uptime")
            if isinstance(ts_ms, (int, float)):
                return ts_ms / 1000.0
    return None


def _probe_assets() -> List[str]:
    """Probe each PROBE_PATH, extract chunk URLs, verify each returns 200.
    Returns list of human-readable failure strings (empty if all green)."""
    failures: List[str] = []
    seen: set[str] = set()
    for path in PROBE_PATHS:
        code, body = _fetch(f"{DASHBOARD_HOST}{path}")
        if code == 0:
            failures.append(f"{path}: unable to fetch (network error)")
            continue
        if code >= 400:
            failures.append(f"{path}: page returned HTTP {code}")
            continue
        html = body.decode("utf-8", "replace")
        for asset in ASSET_RE.findall(html):
            if asset in seen:
                continue
            seen.add(asset)
            acode, _ = _fetch(f"{DASHBOARD_HOST}{asset}")
            if acode != 200:
                failures.append(
                    f"{path}: referenced asset {asset} returned HTTP {acode}"
                )
    return failures


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true",
                   help="exit 1 on any finding (preflight mode)")
    args = p.parse_args()

    if not _dashboard_reachable():
        print("audit_dashboard_live: dashboard not reachable at "
              f"{DASHBOARD_HOST} — skipping (backend-only commit OK)")
        return 0

    issues: List[str] = []

    build_mt = _build_id_mtime()
    proc_start = _pm2_dashboard_start()
    if build_mt is not None and proc_start is not None:
        if build_mt > proc_start + 30:  # 30s grace for build-then-restart race
            from datetime import datetime, timezone
            bt = datetime.fromtimestamp(build_mt, tz=timezone.utc).isoformat()
            pt = datetime.fromtimestamp(proc_start, tz=timezone.utc).isoformat()
            issues.append(
                f"freshness: .next/BUILD_ID ({bt}) is newer than "
                f"wishspark-dashboard PM2 process start ({pt}) — rebuild "
                "happened but no restart. Run `pm2 restart wishspark-dashboard`."
            )

    asset_failures = _probe_assets()
    issues.extend(asset_failures)

    if not issues:
        print("audit_dashboard_live: OK — dashboard serving fresh, all "
              "referenced chunks resolve 200")
        return 0

    print("audit_dashboard_live: ISSUES DETECTED")
    for issue in issues:
        print(f"  ✗ {issue}")
    print()
    print("Fix: cd /opt/wishspark && ./dashboard/scripts/deploy.sh")
    print("(or manually: cd dashboard && npx next build && "
          "pm2 restart wishspark-dashboard)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
