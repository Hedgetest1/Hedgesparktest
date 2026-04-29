#!/usr/bin/env python
"""
audit_oauth_scope_drift.py — preflight invariant.

Catches OAuth scope additions that change the threat model without
explicit review marker. Each new scope expands what the merchant
authorizes HedgeSpark to do — adding `drive` (full Drive) to a flow
that had `drive.file` (only-files-this-app-creates) is a 100×
expansion in attack surface that must NOT slip through silently.

Why it's a bug class
--------------------
OAuth scope strings are easy to add (one literal in a constant) but
each one:
  - Changes the consent-screen text the merchant sees
  - May trigger Google/Slack/Shopify review requirements (e.g.,
    `drive` is sensitive, requires Google verification 4-6w)
  - Expands the radius of damage if the refresh_token is exfiltrated

The doctrine: any change to `_OAUTH_SCOPE` / `_OAUTH_SCOPES` /
`SLACK_SCOPES` / similar constants must be paired with an in-line
comment marker — `# SCOPE-REVIEW: <YYYY-MM-DD> <rationale>` — that
documents the founder-approved threat-model accept.

What this audits
----------------
Walks `app/services/*.py` + `app/api/*.py` for module-level constants
matching `*OAUTH*SCOPE*` / `*OAUTH*SCOPES*`. For each:
  1. Parse the value — split into individual scope tokens.
  2. Check that the SAME line (or the line above) contains a
     `# SCOPE-REVIEW:` marker.
  3. If missing, flag.

Initial baseline
----------------
The two existing OAuth integrations get baseline markers:
  - google_sheets.py::_OAUTH_SCOPE (drive.file + openid + email)
  - shopify_oauth.py SHOPIFY_SCOPES (read_products etc.)
  - merchant_slack.py scope (incoming-webhook only)

Future PR adding a 4th scope to ANY of these = audit blocks until
the SCOPE-REVIEW marker is updated.

Usage
-----
    ./venv/bin/python scripts/audit_oauth_scope_drift.py
    ./venv/bin/python scripts/audit_oauth_scope_drift.py --json
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
SCAN_DIRS = [
    REPO_ROOT / "backend" / "app" / "services",
    REPO_ROOT / "backend" / "app" / "api",
]

# Constant declarations holding OAuth scopes. Heuristic: name contains
# OAUTH or OAUTH_SCOPE; assignment is a string literal or list.
# Match any uppercase const declaration; post-filter on name in Python.
# Earlier regex-only filter missed `_OAUTH_SCOPE` because the leading `_O`
# consumed both `_?` and `[A-Z]`, leaving `AUTH_SCOPE` which doesn't
# contain literal `OAUTH` substring.
_SCOPE_CONST_RE = re.compile(
    r"""^(?P<name>_?[A-Z][A-Z0-9_]*)\s*=\s*(?P<rhs>(?:["'][^"']*["']|\[[^\]]*\]))""",
    re.MULTILINE,
)
def _name_is_oauth_scope(name: str) -> bool:
    """Identifier carries an OAuth scope iff name contains BOTH:
       - `OAUTH` (literal substring) OR provider name (SHOPIFY/GOOGLE/SLACK)
       - `SCOPE` or `SCOPES` suffix
    Excludes false-positives via _FALSE_POSITIVE_NAMES list above.
    """
    if name in _FALSE_POSITIVE_NAMES:
        return False
    upper = name.upper()
    if "SCOPE" not in upper:
        return False
    has_oauth_signal = (
        "OAUTH" in upper
        or "SHOPIFY" in upper
        or "GOOGLE" in upper
        or "SLACK" in upper
        or "KLAVIYO" in upper
    )
    return has_oauth_signal
# Whitelist of constants that LOOK like scope-constants by naming
# convention but are NOT OAuth-related (LLM topic restrictions etc.).
_FALSE_POSITIVE_NAMES = {
    "OUT_OF_SCOPE",      # chat_voice.py — LLM topic restriction list
    "_OUT_OF_SCOPE",     # merchant_chatbot.py — LLM regex patterns
}
_REVIEW_MARKER_RE = re.compile(
    r"""\#\s*SCOPE-REVIEW\s*:\s*(?P<date>\d{4}-\d{2}-\d{2})"""
)


@telemetered("audit_oauth_scope_drift")
def audit() -> int:
    findings: list[dict] = []
    for d in SCAN_DIRS:
        for py_file in d.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            for m in _SCOPE_CONST_RE.finditer(text):
                if not _name_is_oauth_scope(m.group("name")):
                    continue
                lineno = text[: m.start()].count("\n") + 1
                # Check current line + previous 9 lines for SCOPE-REVIEW marker.
                # 9-line window allows for a multi-line rationale comment block
                # (e.g., per-scope justification listing each scope).
                # `lines` is 0-indexed; `lineno` is 1-indexed.
                ctx_lines = lines[max(0, lineno - 10):lineno]
                marker_present = any(
                    _REVIEW_MARKER_RE.search(line) for line in ctx_lines
                )
                if marker_present:
                    continue
                findings.append({
                    "file": str(py_file.relative_to(REPO_ROOT)),
                    "line": lineno,
                    "constant": m.group("name"),
                    "value": m.group("rhs")[:120],
                })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print("✓ all OAuth scope constants have SCOPE-REVIEW markers")
            return 0
        print(f"✗ {len(findings)} OAuth scope constant(s) without review marker:")
        for f in findings:
            print(f"  • {f['file']}:{f['line']}  `{f['constant']}` = {f['value']}")
        print()
        print("Each OAuth scope addition expands the threat model + may trigger")
        print("provider-side review (Google verification, Slack approval).")
        print("Add inline comment within 3 lines of the constant:")
        print("    # SCOPE-REVIEW: 2026-04-29 — reviewed by founder; drive.file")
        print("    # is non-sensitive (no Google verification required), openid+email")
        print("    # is non-sensitive identity for 'Connected as ...' UI display.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
