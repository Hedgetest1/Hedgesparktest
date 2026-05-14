#!/usr/bin/env python
"""
audit_dashboard_redirect_paths.py — preflight invariant.

Catches the exact bug class founder hit on G4 first attempt: backend
code redirecting merchants to a `/app/<path>` URL that doesn't have a
corresponding `dashboard/src/app/app/<path>/page.tsx` file → 404.

What this audits
----------------
For every `RedirectResponse(url=f"{...}/app/<path>...")` or `redirect_to`
with `/app/<path>` in `app/api/**/*.py`, verify:
  - the path resolves to an existing Next.js page in
    dashboard/src/app/app/<path>/page.tsx, OR
  - the path is one of the documented top-level dashboard routes
    (/app, /app/lite, /app/pro, /app/scale, /app/settings, /app/groups,
    /app/reports, /app/marketplace, /app/agency, /app/intelligence,
    /app/operations).

Limitations
-----------
- String-literal extraction only. f"{dashboard}/app/x" with `dashboard`
  resolved at runtime is matched; `f"{dashboard}{var}"` where `var`
  carries the path isn't (would require dataflow analysis).
- Dynamic route segments like `/app/groups/[id]` are matched against
  the page.tsx file at the parent dir.

Usage
-----
    ./venv/bin/python scripts/audit_dashboard_redirect_paths.py
    ./venv/bin/python scripts/audit_dashboard_redirect_paths.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
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
BACKEND_API_DIR = REPO_ROOT / "backend" / "app" / "api"
DASHBOARD_APP_DIR = REPO_ROOT / "dashboard" / "src" / "app" / "app"

# Match `/app/<segment>...` inside f-string interpolations of redirect URLs.
# Specifically targets RedirectResponse(url=...), RedirectResponse url=,
# redirect_to=, success_url=, error_url=, and similar var assignments.
_REDIRECT_RE = re.compile(
    r"""(?:
            RedirectResponse\s*\(\s*url\s*=\s*[\"']      # RedirectResponse(url="
          | RedirectResponse\s*\(\s*url\s*=\s*f[\"']     # RedirectResponse(url=f"
          | (?:redirect_to|success_url|error_url|redirect_url)\s*=\s*f?[\"']
        )
        (?:\{[^}]*\})?            # optional ${var} prefix
        (?P<path>/app/[A-Za-z0-9_\-/\[\]]+)
    """,
    re.VERBOSE,
)

# Top-level dashboard routes that EXIST as page.tsx today.
_KNOWN_TOPLEVEL = {
    "/app",
    "/app/lite",
    "/app/pro",
    "/app/scale",
    "/app/settings",
    "/app/groups",
    "/app/reports",
    "/app/marketplace",
    "/app/agency",
    "/app/intelligence",
    "/app/operations",
}


def _path_to_page_file(path: str) -> pathlib.Path | None:
    """Map /app/foo/bar -> dashboard/src/app/app/foo/bar/page.tsx.

    Strips a trailing /<dynamic-segment> like [id] or {param} when
    resolving — the page.tsx for /app/foo/[id] lives at the parent.
    """
    # Trim trailing /[xxx] or /{xxx} segments
    path = re.sub(r"/\[[^\]]+\]$", "", path)
    path = re.sub(r"/\{[^}]+\}$", "", path)
    if not path.startswith("/app"):
        return None
    rel = path[len("/app"):].lstrip("/")
    if not rel:
        return DASHBOARD_APP_DIR / "page.tsx"
    return DASHBOARD_APP_DIR / rel / "page.tsx"


@telemetered("audit_dashboard_redirect_paths")
def audit() -> int:
    findings: list[dict] = []
    for py_file in BACKEND_API_DIR.rglob("*.py"):
        text = safe_read_text(py_file)
        if text is None:
            continue
        for m in _REDIRECT_RE.finditer(text):
            path = m.group("path")
            # Strip trailing `?...` query-string portion + closing quote
            path = re.split(r"[?\"']", path)[0]
            page_file = _path_to_page_file(path)
            if page_file is None:
                continue
            # Top-level routes are always valid.
            if path in _KNOWN_TOPLEVEL:
                continue
            if not page_file.exists():
                lineno = text[: m.start()].count("\n") + 1
                findings.append({
                    "file": str(py_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "redirect_path": path,
                    "expected_page": str(page_file.relative_to(REPO_ROOT)),
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ all backend redirect paths resolve to dashboard pages")
            return 0
        print(f"✗ {len(findings)} redirect path(s) → 404 in dashboard:")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  redirect={f['redirect_path']}")
            print(f"    expected page at: {f['expected_page']}")
        print()
        print("Fix: either create the page.tsx, or change the backend redirect")
        print("path to an existing one (most common cause: typo or rename drift).")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
