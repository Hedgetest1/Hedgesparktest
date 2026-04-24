#!/usr/bin/env python3
"""audit_session_hook_centralization.py — block direct /merchant/me
or /merchant/plan calls outside the shared useSession hook.

Problem class: a new component or hook implements auth-identity
fetching directly (e.g., calls `apiClient.GET("/merchant/me")`),
bypassing the centralized `useSession` hook. Every such implementation
is a candidate for the "cold cookie → immediate Reconnect prompt"
bug class (detected 2026-04-19 in useSession.ts itself — pre-fix
version of the hook).

This audit enforces the invariant: **exactly one** file in
dashboard/src may call `/merchant/me` or `/merchant/plan` — the
canonical `useSession.ts` hook. Every other file reads session state
via `useSession()`. The hook itself owns the fallback chain
(cookie → localStorage → bootstrap redirect) so no consumer has to
re-implement it.

Coverage claim (honest):
- Catches NEW components/hooks that duplicate session fetching.
- Does NOT catch the legacy `/app/page.tsx` which predates the hook
  and has its own inline session flow. That file is on the Phase 2+
  migration list. Explicit allowlist entry below.

Exit codes:
    0  clean
    1  unauthorized caller found
    2  script error
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"

# Files permitted to call /merchant/me or /merchant/plan directly.
# Every other file must read session state via `useSession()`.
ALLOWLIST: set[str] = {
    # Canonical hook — owns the fallback chain
    "dashboard/src/app/lib/useSession.ts",
    # Legacy main dashboard page. Predates useSession; has its own
    # inline fallback chain; migration tracked for Phase 2+.
    "dashboard/src/app/app/page.tsx",
}

# 2026-04-23 retro DA hardening: accepts single-quoted strings, template
# literals, alternate casings. Multi-line matching supported via DOTALL
# across the full-file scan below — a line-by-line scan misses the
# pattern where someone writes:
#     apiClient
#       .GET("/merchant/me")
# Avoiding overly-broad wrapper matching (e.g. `.sendVerify(...)`) to
# keep false-positive rate near zero; the allowlist is the escape hatch
# for legitimate outliers.
_URL_RE = r"""['"`]/merchant/(?:me|plan)['"`]"""

# Line-scoped patterns — caught by the line-by-line scan.
PATTERNS = [
    re.compile(r'(?i)\bapi[_]?[Cc]lient\.(?:GET|get|POST|post)\(\s*' + _URL_RE),
    re.compile(r'(?i)\bfetch\([^)]*' + _URL_RE),
]

# File-scoped pattern for multi-line apiClient.GET chains. Runs on the
# full file text with DOTALL; catches the two-line form where `apiClient`
# ends a line and `.GET("/merchant/me")` starts the next.
_MULTILINE_PATTERN = re.compile(
    r'(?is)\bapi[_]?[Cc]lient\s*\n\s*\.(?:GET|get|POST|post)\(\s*' + _URL_RE
)


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    findings: list[tuple[int, str]] = []
    # Line-scoped patterns.
    for lineno, line in enumerate(text.splitlines(), start=1):
        for p in PATTERNS:
            if p.search(line):
                findings.append((lineno, line.strip()))
                break
    # Multi-line apiClient.GET chain (e.g. method-chain across 2 lines).
    for m in _MULTILINE_PATTERN.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        snippet = m.group(0).replace("\n", " ").strip()
        findings.append((lineno, f"[multi-line] {snippet}"))
    return findings


@telemetered("audit_session_hook_centralization")
def main(argv: list[str]) -> int:
    if not DASHBOARD_SRC.exists():
        print(
            f"audit_session_hook_centralization: {DASHBOARD_SRC} not found — skip",
            file=sys.stderr,
        )
        return 0

    violations: list[tuple[str, int, str]] = []

    for ext in ("*.ts", "*.tsx"):
        for f in DASHBOARD_SRC.rglob(ext):
            if "node_modules" in f.parts:
                continue
            rel = str(f.relative_to(REPO_ROOT))
            if rel in ALLOWLIST:
                continue
            for lineno, snippet in scan_file(f):
                violations.append((rel, lineno, snippet))

    if not violations:
        print(
            "audit_session_hook_centralization: clean — session "
            "identity fetching is centralized in useSession.ts"
        )
        return 0

    print(
        f"audit_session_hook_centralization: {len(violations)} "
        "unauthorized direct session-fetch call(s)"
    )
    print()
    print("Every file that reads merchant session identity MUST use")
    print("the shared `useSession()` hook from lib/useSession.ts. Only")
    print("useSession itself (and the legacy /app/page.tsx pre-Phase-2")
    print("migration target) may call /merchant/me or /merchant/plan")
    print("directly. Reason: the fallback chain (cookie → localStorage")
    print("→ bootstrap redirect) is the hook's responsibility and must")
    print("not be re-implemented inconsistently per consumer.")
    print()
    for path, lineno, snippet in violations:
        print(f"  {path}:{lineno}  {snippet}")
    print()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(
            f"audit_session_hook_centralization: script error — {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
