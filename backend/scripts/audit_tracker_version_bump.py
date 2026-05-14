#!/usr/bin/env python
"""
audit_tracker_version_bump.py — preflight invariant.

Catches the silent bug class where a merchant's storefront serves
stale cached tracker JS because someone modified `tracker/*.js` but
forgot to bump `TRACKER_VERSION` in `app/core/tracker_version.py`.

Why it's a bug class
--------------------
Storefront tracker is loaded via `<script src="...tracker.js?v={V}">`
where `V = TRACKER_VERSION`. When the constant doesn't change, the
browser's HTTP cache serves the OLD tracker — merchants see new code
on the server but old behavior in production.

CLAUDE.md §8.7 mandates: "Bump on every tracker/*.js change."
Pre-this-audit, the rule was honor-based.

What this audits
----------------
Two modes:
  - **Pre-commit (default):** compares the SET of staged tracker files
    against the staged change to `tracker_version.py`. If any tracker
    file is staged but `tracker_version.py` is NOT staged (or staged
    without a `TRACKER_VERSION = N` change), block.
  - **Standalone (no git context):** compares the timestamp of every
    `tracker/*.js` against `app/core/tracker_version.py`. If any
    tracker file is newer than the version file by >60 seconds, flag.
    (Allows for an in-flight commit window.)

Usage
-----
    ./venv/bin/python scripts/audit_tracker_version_bump.py
    ./venv/bin/python scripts/audit_tracker_version_bump.py --json
    ./venv/bin/python scripts/audit_tracker_version_bump.py --staged
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from _audit_io import safe_read_text

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
TRACKER_DIR = REPO_ROOT / "tracker"
VERSION_FILE = REPO_ROOT / "backend" / "app" / "core" / "tracker_version.py"
_VERSION_RE = re.compile(r"""TRACKER_VERSION\s*=\s*(?P<n>\d+)""")


def _staged_files() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
        )
        return set(result.stdout.strip().split("\n"))
    except Exception:
        return set()


def _read_version() -> int | None:
    try:
        text = VERSION_FILE.read_text()
        m = _VERSION_RE.search(text)
        return int(m.group("n")) if m else None
    except OSError:
        return None


@telemetered("audit_tracker_version_bump")
def audit() -> int:
    findings: list[dict] = []
    use_staged = "--staged" in sys.argv or _staged_files()

    if use_staged:
        staged = _staged_files()
        staged_tracker = {f for f in staged if f.startswith("tracker/") and f.endswith(".js")}
        version_staged = "backend/app/core/tracker_version.py" in staged
        if staged_tracker and not version_staged:
            findings.append({
                "mode": "pre-commit",
                "tracker_files_staged": sorted(staged_tracker),
                "version_file_staged": False,
                "hint": (
                    "Tracker files staged but tracker_version.py is NOT. "
                    "Bump TRACKER_VERSION before commit, otherwise merchants "
                    "serve stale-cached old tracker JS."
                ),
            })
    else:
        # Standalone: timestamp-based check.
        version_mtime = 0.0
        try:
            version_mtime = VERSION_FILE.stat().st_mtime
        except OSError:
            findings.append({
                "mode": "standalone",
                "error": f"version file missing at {VERSION_FILE}",
            })
        else:
            for tracker_js in TRACKER_DIR.glob("*.js"):
                tracker_mtime = tracker_js.stat().st_mtime
                if tracker_mtime > version_mtime + 60:
                    findings.append({
                        "mode": "standalone",
                        "tracker_file": str(tracker_js.relative_to(REPO_ROOT)),
                        "tracker_mtime": tracker_mtime,
                        "version_mtime": version_mtime,
                        "drift_seconds": int(tracker_mtime - version_mtime),
                        "hint": "Tracker file is newer than tracker_version.py by >60s. Bump TRACKER_VERSION.",
                    })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            current = _read_version()
            print(f"✓ TRACKER_VERSION bump in sync (current={current})")
            return 0
        print(f"✗ {len(findings)} TRACKER_VERSION bump issue(s):")
        for f in findings:
            if f.get("mode") == "pre-commit":
                files_str = ", ".join(f["tracker_files_staged"])
                print(f"  • staged tracker files without version bump: {files_str}")
            else:
                print(f"  • {f.get('tracker_file', '?')}: drift {f.get('drift_seconds', '?')}s ahead of version file")
        print()
        print("Fix: edit /opt/wishspark/backend/app/core/tracker_version.py")
        print("     bump TRACKER_VERSION = N+1 and stage the change in the same commit.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
