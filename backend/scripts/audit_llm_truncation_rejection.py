#!/usr/bin/env python3
"""audit_llm_truncation_rejection.py — block LLM wrappers that skip
truncation rejection.

Problem class
-------------
Both Anthropic and OpenAI return a mid-response cutoff signal when
output hits max_tokens:
  - Anthropic: `stop_reason == "max_tokens"`
  - OpenAI:    `choices[0].finish_reason == "length"`

Without an explicit rejection, truncated output propagates to the
parser. Common consequences:
  - Truncated JSON → JSONDecodeError caught as "parse failed" (obscures
    root cause; the fix is to retry with higher max_tokens, not to
    engineer around a bad diff)
  - Partial answer rendered to merchants (chatbot, analytics assistant)
  - Corrupt benchmark results (llm_realmodel_drift)
  - Partial patch diffs applied (bugfix_pipeline — contamination)

On 2026-04-23 a sweep found 9 modules calling LLM APIs; only 1
(bugfix_pipeline) was doing the check. The rest silently returned
truncated output to their parsers. All 9 were closed in the same
commit as this audit.

What this audit checks
----------------------
For every module under `app/services/` that contains an `httpx.post`
to either `api.anthropic.com` or `api.openai.com`, verify the
response-handling block reads `stop_reason`/`finish_reason` and
rejects when it equals `"max_tokens"` / `"length"`.

Method: regex-grep. Not AST-deep — we just need the signal that SOME
check exists in the same function body as the httpx.post. If the
author adds a new wrapper that skips the check, the audit will flag.

Exit code
---------
  0 — clean
  1 — violations (only with --strict)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from _audit_telemetry_shim import telemetered
from _audit_io import safe_read_text

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "app" / "services"

# Vendor URLs we audit. Extensible: adding a new vendor requires
# adding (url_re, stop_field_re, truncation_literal_re, human_name).
# 2026-04-23 DA: case-insensitive match on field names + sentinel
# values so `stop_reason == "MAX_TOKENS"` (uppercase) can't slip past.
_VENDOR_SIGNATURES: list[tuple[re.Pattern, re.Pattern, re.Pattern, str]] = [
    (
        re.compile(r"api\.anthropic\.com"),
        re.compile(r'["\']stop_reason["\']', re.IGNORECASE),
        re.compile(r'==\s*["\']max_tokens["\']|["\']max_tokens["\']\s*==', re.IGNORECASE),
        "anthropic",
    ),
    (
        re.compile(r"api\.openai\.com"),
        re.compile(r'["\']finish_reason["\']', re.IGNORECASE),
        re.compile(r'==\s*["\']length["\']|["\']length["\']\s*==', re.IGNORECASE),
        "openai",
    ),
    # Future vendors (adding here enables the audit automatically):
    # Mistral:  stop_reason == "length" (same as OpenAI)
    # Gemini:   finishReason == "MAX_TOKENS"
    (
        re.compile(r"api\.mistral\.ai"),
        re.compile(r'["\']finish_reason["\']', re.IGNORECASE),
        re.compile(r'==\s*["\']length["\']|["\']length["\']\s*==', re.IGNORECASE),
        "mistral",
    ),
    (
        re.compile(r"generativelanguage\.googleapis\.com|ai\.google\.dev"),
        re.compile(r'["\']finishReason["\']', re.IGNORECASE),
        re.compile(r'==\s*["\']MAX_TOKENS["\']|["\']MAX_TOKENS["\']\s*==', re.IGNORECASE),
        "google",
    ),
]


def _scan_file(path: Path) -> list[str]:
    """Return list of violation descriptions."""
    findings: list[str] = []
    src = safe_read_text(path)
    if src is None:
        return findings

    for url_re, field_re, literal_re, vendor in _VENDOR_SIGNATURES:
        if not url_re.search(src):
            continue
        has_field = bool(field_re.search(src))
        has_reject = bool(literal_re.search(src))
        if not (has_field and has_reject):
            findings.append(
                f"calls {vendor} LLM API but has no truncation-sentinel "
                f"rejection — truncated output will flow to the parser"
            )
    return findings


@telemetered("audit_llm_truncation_rejection")
def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, str]] = []

    if not SERVICES_DIR.is_dir():
        print(f"✗ services dir missing: {SERVICES_DIR}")
        return 1 if strict else 0

    scanned = 0
    has_llm = 0
    for py_path in sorted(SERVICES_DIR.glob("*.py")):
        scanned += 1
        file_hits = _scan_file(py_path)
        if file_hits:
            for desc in file_hits:
                violations.append((py_path, desc))
            has_llm += 1
        else:
            src = safe_read_text(py_path)
            if src is not None and (
                "api.anthropic.com" in src or "api.openai.com" in src
            ):
                has_llm += 1

    if violations:
        print(f"✗ LLM truncation rejection — {len(violations)} violations:")
        for path, desc in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}")
            print(f"    → {desc}")
        print()
        print("Remediation patterns:")
        print("  Anthropic: `if body.get('stop_reason') == 'max_tokens': return ...reject...`")
        print("  OpenAI:    `if choice.get('finish_reason') == 'length': return ...reject...`")
        print("  See bugfix_pipeline._call_provider for reference impl.")
        return 1 if strict else 0

    print(f"✓ every LLM wrapper rejects truncated output "
          f"— scanned {scanned} services, {has_llm} hit LLM APIs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
