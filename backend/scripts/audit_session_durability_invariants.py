#!/usr/bin/env python3
"""
audit_session_durability_invariants.py

Structural preventer for the session-durability E2E suite
(dashboard/e2e/session_durability.spec.ts).

The E2E suite runs against prod and catches session regressions, but
it only fires on a real test run. If someone deletes the retry
backoff in /app/page.tsx or the session_version check in deps.py,
production ships the regression until the next E2E run. This audit
catches the same class at preflight time by asserting the invariants
still exist verbatim in source.

Each invariant here maps 1:1 to an E2E scenario. When editing this
file, also edit the scenario it protects — and vice versa.

Exit code:
  0 — every invariant present in source
  1 — one or more invariants missing (commit blocked)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
DASHBOARD = REPO / "dashboard"

INVARIANTS = [
    # (friendly-name, absolute-path, regex-pattern, failing-E2E-scenario-id)
    (
        "retry backoff on /merchant/me",
        DASHBOARD / "src/app/app/page.tsx",
        r"retryDelaysMs\s*=\s*\[",
        "S6",
    ),
    (
        "hint-cookie recovery path (readHintCookie)",
        DASHBOARD / "src/app/app/page.tsx",
        r"readHintCookie|hs_shop",
        "S2 / S3 / S4 / S5",
    ),
    (
        "bootstrap via /auth/session redirect",
        DASHBOARD / "src/app/app/page.tsx",
        r"bootstrapWithShop|/auth/session\?shop=",
        "S2",
    ),
    (
        "Reconnect UI button copy",
        DASHBOARD / "src/app/app/page.tsx",
        r"Reconnect my store",
        "S7",
    ),
    (
        "useSession retry backoff (shared hook)",
        DASHBOARD / "src/app/lib/useSession.ts",
        r"retryDelaysMs\s*=\s*\[",
        "S6 (hook path)",
    ),
    (
        "session_version mismatch rejection (forced logout)",
        BACKEND / "app/core/deps.py",
        r"token_sv\s*<\s*db_sv",
        "S5",
    ),
    (
        "JWT signature verification via HS256",
        BACKEND / "app/core/merchant_session.py",
        r'algorithms\s*=\s*\["HS256"\]',
        "S3",
    ),
    (
        "JWT expiry enforcement via jwt.decode()",
        BACKEND / "app/core/merchant_session.py",
        r"jwt\.decode\(",
        "S4",
    ),
    (
        "/auth/session unknown-shop → install redirect",
        BACKEND / "app/api/shopify_oauth.py",
        r"RedirectResponse\(\s*url=f?\"[^\"]*\{_APP_URL\}/auth/install",
        "S8",
    ),
    (
        "E2E suite file present",
        DASHBOARD / "e2e/session_durability.spec.ts",
        None,  # existence check only
        "all",
    ),
    (
        "E2E helper file present",
        DASHBOARD / "e2e/helpers/session.ts",
        None,
        "all",
    ),
]


def check_one(name: str, path: Path, pattern: str | None, scenario: str) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing file: {path.relative_to(REPO)}"
    if pattern is None:
        return True, "present"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"unreadable: {exc}"
    if re.search(pattern, text):
        return True, "invariant found"
    return False, f"pattern not found in {path.relative_to(REPO)} (E2E {scenario} would regress)"


def main() -> int:
    failures: list[str] = []
    print("session-durability invariants audit")
    print(f"  repo: {REPO}")
    print(f"  checks: {len(INVARIANTS)}")
    for name, path, pattern, scenario in INVARIANTS:
        ok, msg = check_one(name, path, pattern, scenario)
        status = "✓" if ok else "✗"
        print(f"  {status} [{scenario}] {name}: {msg}")
        if not ok:
            failures.append(f"{scenario}: {name} — {msg}")
    print()
    if failures:
        print(f"BLOCKED — {len(failures)} invariant(s) missing:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("Fix: restore the invariant in source OR update")
        print("  dashboard/e2e/session_durability.spec.ts + this audit")
        print("  to reflect the new design. Never remove invariants blindly.")
        return 1
    print("OK — every session-durability invariant present in source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
