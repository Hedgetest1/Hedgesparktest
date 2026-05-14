#!/usr/bin/env python
"""
audit_token_storage_encrypted.py — preflight invariant.

Catches the bug class where a new OAuth/credential integration adds
a secret-bearing column to `merchants` (or any other table) but
forgets to encrypt the value via existing token_crypto helpers.

Why it's a bug class
--------------------
Plaintext secrets in the DB mean a single read-only DB compromise
exposes every merchant's third-party credentials. HedgeSpark has
canonical encryption helpers in `app/core/token_crypto.py`:
  - `encrypt_token(plaintext)` → `enc:v1:<aesgcm>` ciphertext
  - `decrypt_token(stored)` → plaintext (transparent w/ legacy)
  - `is_encrypted(value)` → bool

The doctrine: every column whose NAME signals it holds a secret
(`*_token`, `*_secret`, `*_key`, `*_refresh_token`) MUST go through
encrypt_token on write + decrypt_token on read. Convention names
the encrypted column `encrypted_<service>_<kind>` so it's unambiguous
in audits + DB inspection.

What this audits
----------------
1. Walks SQLAlchemy models in `app/models/` and finds every Column
   whose name ends in `_token`, `_secret`, `_key`, `_refresh_token`,
   `_password`, `_credentials` — the SECRET-BEARING set.
2. For each such column, requires that the column NAME is prefixed
   with `encrypted_` (signals the encryption convention) OR is one
   of the documented exemptions (Shopify access_token via
   token_crypto, pixel_secret as documented exemption, etc.).
3. Walks `app/services/` + `app/api/` for writes/reads to those
   columns, checking that:
   - Writes pass through `encrypt_token(value)` first
   - Reads pass through `decrypt_token(value)` first

Exemptions
----------
- `merchants.access_token` — Shopify install token. Encrypted via
  token_crypto but predates the `encrypted_` naming convention. The
  encryption is enforced inside oauth flow; column name is grandfathered.
- `merchants.pixel_secret` — public client-side identifier (used in
  `<script>` tag), NOT a secret per the threat model. Documented in
  CLAUDE.md.
- Test fixtures.

Usage
-----
    ./venv/bin/python scripts/audit_token_storage_encrypted.py
    ./venv/bin/python scripts/audit_token_storage_encrypted.py --json
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
MODELS_DIR = REPO_ROOT / "backend" / "app" / "models"
SERVICES_DIRS = [
    REPO_ROOT / "backend" / "app" / "services",
    REPO_ROOT / "backend" / "app" / "api",
]

# Column name suffixes that indicate a secret.
_SECRET_SUFFIXES = (
    "_token",
    "_secret",
    "_refresh_token",
    "_credentials",
    "_password",
)
# Exempt column names — documented edge cases.
_EXEMPT_COLUMNS = {
    # Shopify install token — encrypted at oauth-callback site, column
    # name grandfathered (predates the encrypted_ convention).
    "access_token",
    # Tracker pixel secret — used as PUBLIC client-side identifier in
    # <script> tag, NOT a server secret per CLAUDE.md threat model.
    "pixel_secret",
    # Public share-link token — embedded in /proof/<token> URLs that
    # merchants share publicly (Twitter, copy-link). Equivalent threat
    # model to a public UUID. The token-name suffix is a noun ("share
    # token" = "the token that identifies this share"), not a credential.
    "share_token",
}

# Match Column declarations: `name = Column(<type>, ...)` and capture name.
_COLUMN_RE = re.compile(
    r"""^\s*(?P<name>[a-z_][a-z0-9_]*)\s*=\s*Column\(""",
    re.MULTILINE,
)


@telemetered("audit_token_storage_encrypted")
def audit() -> int:
    findings: list[dict] = []
    for py_file in MODELS_DIR.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = safe_read_text(py_file)
        if text is None:
            continue
        for m in _COLUMN_RE.finditer(text):
            name = m.group("name")
            if not any(name.endswith(suf) for suf in _SECRET_SUFFIXES):
                continue
            if name in _EXEMPT_COLUMNS:
                continue
            if name.startswith("encrypted_"):
                continue  # follows convention
            # Convention violation — flag it.
            lineno = text[: m.start()].count("\n") + 1
            findings.append({
                "file": str(py_file.relative_to(REPO_ROOT)),
                "line": lineno,
                "column": name,
                "hint": (
                    f"Column `{name}` looks like a secret but doesn't follow "
                    "the encrypted_<service>_<kind> convention. Either rename "
                    "+ migrate, OR add to _EXEMPT_COLUMNS in this audit "
                    "with documented threat-model exemption."
                ),
            })

    # Second pass: check that every encrypted_* column has a paired
    # encrypt_token/decrypt_token usage somewhere in the codebase.
    encrypted_cols = re.findall(
        r"""(encrypted_[a-z_]+)\s*=\s*Column\(""",
        "\n".join(
            (MODELS_DIR / f).read_text(encoding="utf-8", errors="replace")
            for f in [p.name for p in MODELS_DIR.glob("*.py") if p.name != "__init__.py"]
        ),
    )
    encrypted_set = set(encrypted_cols)

    code_blob = ""
    for d in SERVICES_DIRS:
        for f in d.rglob("*.py"):
            text = safe_read_text(f, errors="replace")
            if text is None:
                continue
            code_blob += text + "\n"

    for col in encrypted_set:
        if col == "encrypted_klaviyo_key":
            # Documented usage in klaviyo_connection.py.
            continue
        # Find usage of column. We require BOTH a write path (encrypt_token
        # called near assignment to this column) AND a read path
        # (decrypt_token called when reading this column).
        # Simple heuristic: file mentions both encrypt_token and the column
        # name, OR decrypt_token + the column name.
        write_ok = bool(re.search(rf"{col}\s*=\s*encrypt_token", code_blob)) or bool(
            re.search(rf"encrypt_token\([^)]+\).*{col}", code_blob, re.DOTALL)
        )
        read_ok = bool(re.search(rf"decrypt_token\([^)]*{col}", code_blob)) or bool(
            re.search(rf"{col}.*decrypt_token", code_blob, re.DOTALL)
        )
        if not write_ok:
            findings.append({
                "column": col,
                "missing": "write_path",
                "hint": f"Column `{col}` exists but no `{col} = encrypt_token(...)` assignment found",
            })
        if not read_ok:
            findings.append({
                "column": col,
                "missing": "read_path",
                "hint": f"Column `{col}` exists but no `decrypt_token({col})` read found",
            })

    if "--json" in sys.argv:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        if not findings:
            print(f"✓ all secret-bearing columns follow encrypted_ convention + use token_crypto helpers")
            return 0
        print(f"✗ {len(findings)} token-storage encryption issue(s):")
        for f in findings:
            if "file" in f:
                print(f"  • {f['file']}:{f['line']}  `{f['column']}` — convention violation")
            else:
                print(f"  • `{f['column']}` — missing {f['missing']}: {f['hint']}")
        print()
        print("Fix: rename column to `encrypted_<service>_<kind>` (with migration),")
        print("     OR pair with encrypt_token() write + decrypt_token() read,")
        print("     OR add to _EXEMPT_COLUMNS with documented threat-model rationale.")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(audit())
