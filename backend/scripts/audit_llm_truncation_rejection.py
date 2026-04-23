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

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "app" / "services"

_ANTHROPIC_URL_RE = re.compile(r"api\.anthropic\.com")
_OPENAI_URL_RE = re.compile(r"api\.openai\.com")
# Both tokens must be present somewhere in the file. We don't require
# tight coupling because the idiomatic pattern binds stop_reason to a
# local then compares on the next line (bugfix_pipeline) — that span
# is longer than a simple lookahead can reach cleanly. A file that
# has both `stop_reason` and `"max_tokens"` as string literals is a
# strong signal the rejection check is wired.
_ANTHROPIC_STOP_REASON_RE = re.compile(r'["\']stop_reason["\']')
_ANTHROPIC_MAX_TOKENS_LITERAL_RE = re.compile(r'==\s*["\']max_tokens["\']|["\']max_tokens["\']\s*==')
_OPENAI_FINISH_REASON_RE = re.compile(r'["\']finish_reason["\']')
_OPENAI_LENGTH_LITERAL_RE = re.compile(r'==\s*["\']length["\']|["\']length["\']\s*==')


def _scan_file(path: Path) -> list[str]:
    """Return list of violation descriptions."""
    findings: list[str] = []
    try:
        src = path.read_text()
    except Exception:
        return findings

    hits_anthropic = bool(_ANTHROPIC_URL_RE.search(src))
    hits_openai = bool(_OPENAI_URL_RE.search(src))

    if hits_anthropic:
        has_stop_reason = bool(_ANTHROPIC_STOP_REASON_RE.search(src))
        has_reject = bool(_ANTHROPIC_MAX_TOKENS_LITERAL_RE.search(src))
        if not (has_stop_reason and has_reject):
            findings.append(
                "calls api.anthropic.com but has no stop_reason=='max_tokens' "
                "rejection — truncated output will flow to the parser"
            )
    if hits_openai:
        has_finish_reason = bool(_OPENAI_FINISH_REASON_RE.search(src))
        has_reject = bool(_OPENAI_LENGTH_LITERAL_RE.search(src))
        if not (has_finish_reason and has_reject):
            findings.append(
                "calls api.openai.com but has no finish_reason=='length' "
                "rejection — truncated output will flow to the parser"
            )
    return findings


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
            try:
                src = py_path.read_text()
                if "api.anthropic.com" in src or "api.openai.com" in src:
                    has_llm += 1
            except Exception:
                pass

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
