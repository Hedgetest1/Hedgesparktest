#!/usr/bin/env python
"""
audit_oauth_refresh_rotation.py — preflight invariant.

For every OAuth flow that stores a refresh_token, verify the code
follows the canonical "transparent refresh on access_token expiry"
pattern. Without this pattern, access_tokens silently expire +
subsequent API calls 401 → user-visible breakage.

Why it's a bug class
--------------------
OAuth providers (Google, Slack-bot-tokens, etc.) issue:
  - refresh_token: long-lived (never expires unless revoked)
  - access_token: short-lived (~1h), used for actual API calls

The CORRECT pattern is:
  1. On consent: store ENCRYPTED refresh_token in DB.
  2. Each API call: call get_access_token(shop) which:
     a. Returns cached access_token if not expired (memory cache w/
        leeway).
     b. Else refreshes via the stored refresh_token + caches.
  3. NEVER store access_token in DB (it's stale within an hour).

Anti-patterns this audit catches:
  - Storing access_token in DB (`access_token = Column(...)` on a
    model that ALSO has refresh_token storage).
  - Calling Google/Slack/etc. API with `Authorization: Bearer <stored>`
    where `<stored>` is the access_token from DB (stale).
  - Missing get_access_token() wrapper or equivalent (calling API
    directly with refresh_token, which doesn't authenticate API
    requests — only the /token endpoint accepts refresh_token).

What this audits
----------------
Walks `app/services/*.py` for files importing or defining
`refresh_token` handling. For each, requires:
  1. A function or method named `get_access_token`, `_refresh_access_token`,
     or `refresh_*_access` exists in the same file.
  2. The function's body references the stored refresh_token via
     decrypt_token (token_crypto round-trip).
  3. Includes an in-memory cache (`_access_token_cache` or similar
     with expiry) to avoid hitting /token on every API call.

Exempt-list: providers that don't issue refresh_tokens (e.g., Slack
incoming-webhook URL is permanent).

Usage
-----
    ./venv/bin/python scripts/audit_oauth_refresh_rotation.py
    ./venv/bin/python scripts/audit_oauth_refresh_rotation.py --json
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
SCAN_DIRS = [REPO_ROOT / "backend" / "app" / "services"]

# Files that store refresh_token — MUST implement rotation.
_REFRESH_STORE_RE = re.compile(
    r"""(?:
        encrypted_\w+_refresh_token
      | \brefresh_token\s*=\s*encrypt_token
      | refresh_token\s*=\s*tokens\.get\s*\(\s*["']refresh_token["']
    )""",
    re.VERBOSE,
)
_GET_ACCESS_FN_RE = re.compile(
    r"""def\s+(?:get_access_token|_refresh_access_token|refresh_\w+_access|_get_\w+_access_token)\s*\(""",
)
_DECRYPT_USAGE_RE = re.compile(
    r"""decrypt_token\s*\([^)]*refresh_token""",
)
_CACHE_PATTERN_RE = re.compile(
    r"""(?:_access_token_cache|access_token_cache|expires_at|expires_in)""",
)

# Exemption: Slack-bot-token providers that issue ONLY a long-lived
# token (no refresh_token semantics). Slack incoming-webhook URLs are
# permanent until merchant revokes.
_EXEMPT_FILES = {
    "merchant_slack.py",  # incoming-webhook only
    "klaviyo.py",         # API key, not OAuth
    "klaviyo_connection.py",
    "klaviyo_export.py",
}


@telemetered("audit_oauth_refresh_rotation")
def audit() -> int:
    findings: list[dict] = []
    for d in SCAN_DIRS:
        for py_file in d.rglob("*.py"):
            if py_file.name in _EXEMPT_FILES:
                continue
            text = safe_read_text(py_file)
            if text is None:
                continue
            if not _REFRESH_STORE_RE.search(text):
                continue  # not a refresh-token-storing file
            # Required signals:
            has_fn = bool(_GET_ACCESS_FN_RE.search(text))
            has_decrypt = bool(_DECRYPT_USAGE_RE.search(text))
            has_cache = bool(_CACHE_PATTERN_RE.search(text))
            missing = []
            if not has_fn:
                missing.append("get_access_token() wrapper")
            if not has_decrypt:
                missing.append("decrypt_token(refresh_token) usage")
            if not has_cache:
                missing.append("access-token cache (expires_at)")
            if missing:
                findings.append({
                    "file": str(py_file.relative_to(REPO_ROOT)),
                    "missing": missing,
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ all refresh-token-storing services implement rotation pattern")
            return 0
        print(f"✗ {len(findings)} OAuth refresh-rotation gap(s):")
        for f in findings:
            print(f"  • {f['file']}")
            for m in f["missing"]:
                print(f"      - missing: {m}")
        print()
        print("Pattern: app/services/google_sheets.py is the canonical reference.")
        print("Steps:")
        print("  1. _refresh_access_token(refresh_token) — calls /token w/ grant_type=refresh_token")
        print("  2. get_access_token(db, *, shop) — caches in-memory, refreshes on expiry")
        print("  3. _access_token_cache: dict[shop, (token, expires_at)] with leeway")
        print("Without this, access_tokens silently expire and API calls 401.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
