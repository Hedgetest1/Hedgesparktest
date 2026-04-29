#!/usr/bin/env python
"""
audit_oauth_state_redis_backed.py — preflight invariant.

Catches the bug class shipped in google_oauth.py before the 2026-04-29
multi-worker promotion: OAuth state stored in a module-level dict
instead of Redis. Worst case at scale: state token generated in worker
1 + callback hits worker 2 → `state_unknown` → 100% retry needed.

Why it's a bug class
--------------------
HedgeSpark backend runs 4 uvicorn workers (per CLAUDE.md §6). Any
state token generated in worker N must be retrievable by worker M.
In-memory dicts are per-process — module-level state ALWAYS multi-
worker-broken in production. The only safe pattern is Redis-backed
storage with TTL, atomic getdel() consume.

What this audits
----------------
Walks `app/api/*.py` for OAuth-style endpoints (any function with
`oauth` or `auth/google|slack|shopify|klaviyo` in path or import that
references `state` or `csrf_token`). Flags every module-level dict
declaration that:
  1. Is named *state*, *oauth*, *csrf* (typical CSRF-state holders)
  2. Has type annotation `dict[...]` or assignment `= {}`
  3. Is NOT prefixed with `# multi-worker:` annotation that explicitly
     covers the case (accept-degrade explanation OR redis-backed)

Exemptions
----------
- Module-level dicts ANNOTATED `# multi-worker: <reason>` are allowed
  but only if reason is explicitly justified (per
  audit_multiworker_safety.py rules).
- Test fixtures in tests/* — skip.
- Constants (UPPERCASE names, `_LOOKUP` suffix) — skip.

Usage
-----
    ./venv/bin/python scripts/audit_oauth_state_redis_backed.py
    ./venv/bin/python scripts/audit_oauth_state_redis_backed.py --json
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    from _audit_telemetry_shim import telemetered
except Exception:
    def telemetered(name):  # type: ignore[no-redef]
        def deco(fn):
            return fn
        return deco


REPO_ROOT = pathlib.Path("/opt/wishspark")
SCAN_DIRS = [REPO_ROOT / "backend" / "app" / "api"]

# Match module-level dict declarations with state/oauth/csrf in name.
_STATE_DICT_RE = re.compile(
    r"""(?P<full_line>
            ^(?P<name>_?[a-z][a-z_]*(?:state|oauth|csrf|nonce)[a-z_]*)
            \s*(?::\s*[Dd]ict[^=]*)?\s*=\s*\{\}
        )""",
    re.MULTILINE | re.VERBOSE,
)

# Detect file is OAuth-related — by router prefix, scope, callback,
# state-management imports.
_OAUTH_FILE_RE = re.compile(
    r"""(?:
        oauth_state
      | /auth/google
      | /auth/slack
      | /auth/shopify
      | /oauth2
      | build_authorization_url
      | exchange_code_for_tokens
    )""",
    re.VERBOSE,
)

# Acceptable annotation immediately above (or on the previous line):
_ANNOTATION_RE = re.compile(
    r"""\#\s*multi-worker\s*:\s*(?P<flavor>redis-backed|redis-mirrored|accept-degrade|constant|thread-only|persistent)"""
)


@telemetered("audit_oauth_state_redis_backed")
def audit() -> int:
    findings: list[dict] = []
    for d in SCAN_DIRS:
        for py_file in d.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not _OAUTH_FILE_RE.search(text):
                continue  # not OAuth-related
            lines = text.splitlines()
            for m in _STATE_DICT_RE.finditer(text):
                name = m.group("name")
                # Skip uppercase constants (LOOKUP_TABLES, etc.)
                if name.isupper() or name.endswith("_LOOKUP") or name.endswith("_TABLE"):
                    continue
                lineno = text[: m.start()].count("\n") + 1
                # Check the previous 5 lines for the annotation.
                ctx_start = max(0, lineno - 6)
                ctx_lines = lines[ctx_start:lineno]
                annotated = any(_ANNOTATION_RE.search(line) for line in ctx_lines)
                if annotated:
                    continue
                findings.append({
                    "file": str(py_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "var_name": name,
                    "hint": "OAuth state in unannotated module-level dict — multi-worker-broken",
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ no unannotated OAuth state dicts in app/api")
            return 0
        print(f"✗ {len(findings)} OAuth state in module-level dict (multi-worker hazard):")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  `{f['var_name']}` — {f['hint']}")
        print()
        print("Fix: store OAuth state in Redis with TTL + atomic getdel() consume.")
        print("Pattern: app/api/google_oauth.py::_store_oauth_state + _consume_oauth_state.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
