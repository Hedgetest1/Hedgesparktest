#!/usr/bin/env python3
"""Critical-secrets consistency preventer.

Catches the bug class where `app/core/auth_hardening.py::_CRITICAL_SECRETS`
references an environment-variable name that does NOT actually exist as
`os.getenv("<NAME>")` anywhere in the codebase — meaning the ops endpoint
`/ops/auth/posture` reports the secret as "missing" even when the real
secret IS configured under a different name.

Born from the 2026-05-02 multidim sweep that surfaced TWO drifted entries
(MERCHANT_SESSION_SIGNING_KEY → MERCHANT_SESSION_SECRET, and
TOKEN_ENCRYPTION_KEY → MERCHANT_TOKEN_ENCRYPTION_KEY).

Failure: at least one entry in _CRITICAL_SECRETS has a name that no
`os.getenv("<name>")` / `os.environ.get("<name>")` / `os.environ["<name>"]`
call uses anywhere under app/.

Usage:
    python3 scripts/audit_critical_secrets_consistency.py
    Exit 0 = clean. Exit 1 = drift detected.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from _audit_io import safe_read_text

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
TARGET = REPO / "app" / "core" / "auth_hardening.py"


def parse_critical_secrets(path: Path) -> list[str]:
    """Extract env-var names from _CRITICAL_SECRETS list literal.

    Handles both `_CRITICAL_SECRETS = [...]` (Assign) and
    `_CRITICAL_SECRETS: list[...] = [...]` (AnnAssign).
    """
    def _names_from_list(value: ast.AST) -> list[str]:
        if not isinstance(value, ast.List):
            return []
        out: list[str] = []
        for el in value.elts:
            if isinstance(el, ast.Tuple) and el.elts:
                first = el.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    out.append(first.value)
        return out

    src = safe_read_text(path)
    if src is None:
        return []
    tree = ast.parse(src, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_CRITICAL_SECRETS":
                    return _names_from_list(node.value)
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
            if (
                isinstance(tgt, ast.Name)
                and tgt.id == "_CRITICAL_SECRETS"
                and node.value is not None
            ):
                return _names_from_list(node.value)
    return []


def collect_env_var_names_used(root: Path) -> set[str]:
    """Scan every .py under app/ and harvest the literal first arg to
    os.getenv / os.environ.get and the literal subscript to os.environ[].
    """
    pat_getenv = re.compile(r'os\.getenv\(\s*[\'"]([A-Z_][A-Z0-9_]*)[\'"]')
    pat_env_get = re.compile(r'os\.environ\.get\(\s*[\'"]([A-Z_][A-Z0-9_]*)[\'"]')
    pat_env_sub = re.compile(r'os\.environ\[\s*[\'"]([A-Z_][A-Z0-9_]*)[\'"]\s*\]')
    used: set[str] = set()
    for py in root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = safe_read_text(py)
        if text is None:
            continue
        for pat in (pat_getenv, pat_env_get, pat_env_sub):
            used.update(pat.findall(text))
    return used


def main() -> int:
    if not TARGET.exists():
        print(f"FAIL: target file not found: {TARGET}")
        return 1
    secrets = parse_critical_secrets(TARGET)
    if not secrets:
        print(f"FAIL: could not parse _CRITICAL_SECRETS from {TARGET}")
        return 1
    used = collect_env_var_names_used(APP)
    orphans = [s for s in secrets if s not in used]
    if orphans:
        print(
            "FAIL: _CRITICAL_SECRETS lists env-var name(s) never read as "
            "os.getenv/os.environ in app/:"
        )
        for name in orphans:
            print(f"  - {name}")
        print(
            "\nThis means /ops/auth/posture reports the secret as "
            "'missing' even when configured under a different name. "
            "Fix the entry in app/core/auth_hardening.py::_CRITICAL_SECRETS "
            "or wire the actual env var read."
        )
        return 1
    print(
        f"OK: all {len(secrets)} _CRITICAL_SECRETS entries are read "
        f"as os.getenv/os.environ somewhere in app/."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
