#!/usr/bin/env python3
"""Class-of-class env-var registry preventer.

Generalises audit_critical_secrets_consistency to ALL env-var
registries in the codebase, not just auth_hardening._CRITICAL_SECRETS.
Catches the bug class where ANY list/tuple/dict of env-var names
drifts from the actual os.getenv reads — same root cause as the
2026-05-02 MERCHANT_SESSION_SIGNING_KEY → MERCHANT_SESSION_SECRET
drift, but generalised.

Targets module-level Assign / AnnAssign nodes whose target name
matches one of these registry-shape patterns:
    *_SECRETS         e.g. _CRITICAL_SECRETS
    *_KEYS            e.g. ENCRYPTION_KEYS
    *_ENV_VARS        e.g. REQUIRED_ENV_VARS
    *_REQUIRED_ENV    e.g. PROD_REQUIRED_ENV
    *ENV_REGISTRY     e.g. SETTINGS_ENV_REGISTRY

For each match, walks the value (List/Tuple/Dict) and collects
UPPER_SNAKE string literals (length 6+, plausible env-var names).
Verifies each is read as os.getenv("<name>") / os.environ.get
/ os.environ[] somewhere under app/. Orphan names → FAIL.

Born 2026-05-02 from the brutal-CTO 10/10 sprint. The single-
class audit (audit_critical_secrets_consistency) caught the
specific drift in auth_hardening._CRITICAL_SECRETS; this audit
catches the SAME bug class anywhere it might appear.

Usage:
    python3 scripts/audit_env_var_registries_consistency.py
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

# Identifier patterns that mark a binding as "env-var registry shape".
# Match on substring so prefixed/decorated names are caught
# (_CRITICAL_SECRETS, REQUIRED_ENV_VARS, etc.).
_REGISTRY_NAME_PATTERNS = [
    re.compile(r"^_*[A-Z][A-Z0-9_]*SECRETS?$"),
    re.compile(r"^_*[A-Z][A-Z0-9_]*KEYS$"),
    re.compile(r"^_*[A-Z][A-Z0-9_]*ENV_VARS?$"),
    re.compile(r"^_*[A-Z][A-Z0-9_]*REQUIRED_ENV$"),
    re.compile(r"^_*[A-Z][A-Z0-9_]*ENV_REGISTRY$"),
    re.compile(r"^_*[A-Z][A-Z0-9_]*REQUIRED_SECRETS$"),
]

# Plausible env-var name = UPPER_SNAKE, length 6+, no digits-first.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{5,}$")

# Words that look UPPER_SNAKE but are domain enums, not env vars
_ENUM_LIKE_TOKENS = frozenset({
    "TRUE", "FALSE", "NULL", "NONE", "OK", "FAIL",
    "GET", "POST", "PUT", "PATCH", "DELETE",
    "DEFAULT", "PRIMARY", "SECONDARY", "OPTIONAL", "REQUIRED",
    "WARNING", "CRITICAL", "INFO", "DEBUG", "ERROR",
    "ACTIVE", "INACTIVE", "PENDING", "RESOLVED",
})


def is_registry_name(name: str) -> bool:
    return any(p.match(name) for p in _REGISTRY_NAME_PATTERNS)


def collect_string_literals(node: ast.AST) -> list[str]:
    """Walk the value AST and collect every str-typed Constant."""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def collect_env_var_names_used(root: Path) -> set[str]:
    """Scan every .py and harvest the literal first arg to
    os.getenv / os.environ.get and the literal subscript to
    os.environ[]."""
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


def find_registries() -> list[tuple[Path, str, list[str]]]:
    """Return (file, registry_name, env_var_names) for every module-
    level binding matching a registry-shape pattern."""
    out: list[tuple[Path, str, list[str]]] = []
    for py in APP.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        src = safe_read_text(py)
        if src is None:
            continue
        try:
            tree = ast.parse(src, filename=str(py))
        except SyntaxError:
            continue
        for node in ast.iter_child_nodes(tree):
            target_name: str | None = None
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    target_name = node.targets[0].id
                    value = node.value
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.value is not None:
                    target_name = node.target.id
                    value = node.value
            if target_name is None or value is None:
                continue
            if not is_registry_name(target_name):
                continue
            literals = collect_string_literals(value)
            env_names = sorted(set(
                lit for lit in literals
                if _ENV_NAME_RE.match(lit) and lit not in _ENUM_LIKE_TOKENS
            ))
            if env_names:
                out.append((py, target_name, env_names))
    return out


def main() -> int:
    used = collect_env_var_names_used(APP)
    registries = find_registries()
    if not registries:
        print(
            "OK: no env-var registries found (no module-level binding "
            "matched the *_SECRETS / *_KEYS / *_ENV_VARS naming pattern)."
        )
        return 0

    drift: list[tuple[Path, str, list[str]]] = []
    total_names = 0
    for py, target, names in registries:
        total_names += len(names)
        orphan = [n for n in names if n not in used]
        if orphan:
            drift.append((py, target, orphan))

    if drift:
        print(
            "FAIL: env-var registry drift — name(s) listed but never "
            "read as os.getenv/os.environ in app/:"
        )
        for py, target, orphan in drift:
            rel = py.relative_to(REPO)
            print(f"  {rel} :: {target}")
            for name in orphan:
                print(f"      - {name}")
        print(
            "\nFix: rename the orphan to the actual env var name, "
            "OR remove from the registry, OR wire the missing read."
        )
        return 1

    print(
        f"OK: {len(registries)} env-var registry/registries scanned, "
        f"{total_names} env name(s), all read somewhere in app/."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
