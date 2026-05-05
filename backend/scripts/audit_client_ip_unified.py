#!/usr/bin/env python3
"""audit_client_ip_unified.py — preventer for client-IP extraction drift.

Doctrine: every site that needs the real client IP MUST go through
`app/core/client_ip.py::extract_client_ip(request)`. Direct reads of
`request.client.host` or `request.headers.get("x-forwarded-for")` /
`request.headers.get("cf-connecting-ip")` outside the helper bypass
the Cloudflare-aware precedence (CF-Connecting-IP → XFF first hop →
socket peer) and silently regress under CDN flip.

The 2026-05-05 sprint unified 7 sites onto the helper PRECISELY to
make Cloudflare migration a configuration event rather than a
code-rewrite event. This audit pins that invariant: any new site
that forgets the helper fails preflight before commit.

Detection: ripgrep / grep for the forbidden patterns in `app/`,
exclude the helper itself, exclude opt-out-tagged lines.

Allowlist:
  - `app/core/client_ip.py` — the helper itself (only allowed reader)

Annotation opt-out: add a comment on the same line OR the line above
matching `client-ip: ok — <reason>`. Reasons must be specific
(e.g. "websocket scope, no Request available").
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

# Exact file allow: the helper itself.
ALLOWLIST_FILES = {APP_DIR / "core" / "client_ip.py"}

# Patterns that, when seen in app/ outside the helper, indicate drift.
# Each is a regex; finding ANY hit on a non-comment line = drift.
FORBIDDEN_PATTERNS = [
    # Direct socket-peer read.
    re.compile(r"request\.client\.host"),
    # Direct XFF read (any case).
    re.compile(r"""request\.headers\.get\(\s*['"]x-forwarded-for['"]""", re.IGNORECASE),
    # Direct CF-Connecting-IP read.
    re.compile(r"""request\.headers\.get\(\s*['"]cf-connecting-ip['"]""", re.IGNORECASE),
]

OPT_OUT_RE = re.compile(r"#\s*client-ip:\s*ok\b", re.IGNORECASE)


def _is_optout(line: str, prev_line: str) -> bool:
    return bool(OPT_OUT_RE.search(line) or OPT_OUT_RE.search(prev_line))


def audit_file(path: Path) -> list[tuple[int, str]]:
    if path in ALLOWLIST_FILES:
        return []
    try:
        src = path.read_text()
    except Exception:
        return []
    lines = src.split("\n")
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        # Skip pure-comment lines (often docstrings / commented-out examples)
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for pat in FORBIDDEN_PATTERNS:
            if pat.search(line):
                prev = lines[i - 2] if i >= 2 else ""
                if _is_optout(line, prev):
                    break
                findings.append((i, line.strip()[:140]))
                break
    return findings


@telemetered("audit_client_ip_unified")
def main() -> int:
    strict = "--strict" in sys.argv
    total = 0
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for py in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        findings = audit_file(py)
        if findings:
            by_file[py] = findings
            total += len(findings)

    if total == 0:
        print("✅ no client-IP extraction drift — every site routes via core/client_ip.py.")
        return 0

    for fpath, findings in by_file.items():
        rel = fpath.relative_to(REPO_ROOT)
        for line_no, snippet in findings:
            print(
                f"  ⚠️  {rel}:{line_no} — bypasses extract_client_ip(): {snippet}"
            )

    print(
        f"\n{total} forbidden client-IP read(s) outside app/core/client_ip.py.\n"
        f"Replace with: from app.core.client_ip import extract_client_ip\n"
        f"             ip = extract_client_ip(request)\n"
        f"Or annotate `# client-ip: ok — <reason>` on the offending line.\n"
    )
    return 1 if strict else 1  # always non-zero on findings


if __name__ == "__main__":
    sys.exit(main())
