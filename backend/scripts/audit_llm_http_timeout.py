#!/usr/bin/env python3
"""audit_llm_http_timeout.py — block unbounded httpx LLM calls.

Problem class
-------------
`httpx.post(...)` defaults to NO timeout. If a service calls the
Anthropic or OpenAI API and forgets to pass `timeout=...`, a hung
provider TCP connection can block the caller forever:
  - worker cycles stall
  - request handlers tie up uvicorn workers
  - session tokens time out while we wait for an LLM

Within-module timeout VALUES are intentionally varied (15s for
orchestrator action selection, 60s for Opus strategic audits, etc.
— complexity-vs-SLA tradeoff). This audit does NOT enforce a specific
value; it enforces presence.

What this audit checks
----------------------
For every `httpx.post(...)` call that targets an LLM API URL
(api.anthropic.com or api.openai.com), verify the call passes a
`timeout=` keyword argument.

Uses AST. A missing timeout is a HARD FAIL under --strict.

Exit code
---------
  0 — clean
  1 — unbounded call found (--strict)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = REPO_ROOT / "app" / "services"

_LLM_URL_MARKERS = ("api.anthropic.com", "api.openai.com")


def _is_llm_url(node: ast.AST) -> bool:
    """Return True if the first positional arg is an LLM API URL literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return any(marker in node.value for marker in _LLM_URL_MARKERS)
    if isinstance(node, ast.JoinedStr):  # f-string
        return any(
            isinstance(v, ast.Constant) and any(m in (v.value or "") for m in _LLM_URL_MARKERS)
            for v in node.values
        )
    return False


def _has_timeout_kwarg(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "timeout":
            return True
    return False


def _scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        src = path.read_text()
        tree = ast.parse(src, filename=str(path))
    except Exception:
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Narrow to httpx.post(...)
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "post"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not _is_llm_url(first_arg):
            continue
        if not _has_timeout_kwarg(node):
            findings.append((node.lineno, "httpx.post to LLM API without timeout="))
    return findings


def main() -> int:
    strict = "--strict" in sys.argv
    violations: list[tuple[Path, int, str]] = []
    scanned = 0

    if not SERVICES_DIR.is_dir():
        print(f"✗ services dir missing: {SERVICES_DIR}")
        return 1 if strict else 0

    for py_path in sorted(SERVICES_DIR.glob("*.py")):
        scanned += 1
        for lineno, desc in _scan_file(py_path):
            violations.append((py_path, lineno, desc))

    if violations:
        print(f"✗ LLM HTTP timeout — {len(violations)} unbounded calls:")
        for path, lineno, desc in violations:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}:{lineno}  {desc}")
        print()
        print("Every httpx.post() to api.anthropic.com or api.openai.com")
        print("MUST pass `timeout=<seconds>`. Choose a value appropriate to")
        print("the prompt complexity (15s for short decisions, 60s for")
        print("Opus strategic audits). Never leave unbounded.")
        return 1 if strict else 0

    print(f"✓ every LLM httpx.post has a timeout — scanned {scanned} services")
    return 0


if __name__ == "__main__":
    sys.exit(main())
