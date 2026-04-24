#!/usr/bin/env python3
"""audit_claude_md_redis_keys.py — keep CLAUDE.md §13 in sync with the
actual Redis keys used by the backend.

Problem class: CLAUDE.md §13 catalogs every Redis key pattern with its
purpose + TTL. That table is load-bearing for the 10k-merchant scale
checklist (§12: "Every Redis key has a TTL"). If a new key lands in
code without a table row, TTL review never happens.

This script scans `app/` for every string literal and f-string static
prefix starting with `hs:` or `llm:`, extracts the stable prefix (up
to the first variable), and cross-checks against the table.

Detections:
    1. Prefix used in code but MISSING from table → add a row with TTL.
    2. Prefix in table but UNUSED in code → remove the row (or the
       code was deleted without cleanup).

Exit codes:
    0  map in sync
    1  drift detected
    2  script error

Use `--warn-only` to print without failing.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
APP_ROOT = BACKEND_ROOT / "app"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Redis key families we track. Extend only when a new system
# introduces a new top-level namespace (don't sub-namespace existing
# ones here — that's a table row change, not a scanner change).
KEY_FAMILIES = ("hs:", "llm:")


def _stable_prefix(raw: str) -> str:
    """Collapse a key pattern / partial-key literal to its variable-free
    prefix. Used for BOTH CLAUDE.md entries and code strings so the
    comparison is apples-to-apples.

    Examples:
        'hs:trkerr:tot:{shop}:{date}' → 'hs:trkerr:tot'
        'hs:trkerr:tot:'              → 'hs:trkerr:tot'
        'hs:segmon:cursor'            → 'hs:segmon:cursor'
        'llm:monthly_cost:{month}'    → 'llm:monthly_cost'
    """
    # Cut at first `{` (variable substitution marker).
    idx = raw.find("{")
    if idx > 0:
        raw = raw[:idx]
    # Strip trailing colons that come from f-string static heads.
    return raw.rstrip(":")


def _extract_doc_prefixes(md_text: str) -> set[str]:
    """Parse CLAUDE.md §13 table, return stable prefixes from the
    first cell of each data row."""
    prefixes: set[str] = set()
    in_section = False
    for line in md_text.splitlines():
        if line.startswith("## 13. Redis keys"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        # Match first-cell inline-code: | `hs:foo:{x}` | ...
        m = re.match(r"\s*\|\s*`([^`]+)`", line)
        if not m:
            continue
        raw = m.group(1)
        if raw.startswith(KEY_FAMILIES):
            prefixes.add(_stable_prefix(raw))
    return prefixes


def _extract_code_prefixes(app_root: Path) -> dict[str, list[tuple[str, int]]]:
    """Walk every .py file in app_root; return map prefix → [(file, line), ...]."""
    found: dict[str, list[tuple[str, int]]] = {}

    for py in app_root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue

        rel = str(py.relative_to(BACKEND_ROOT))

        for node in ast.walk(tree):
            # Case 1: plain string constant.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(KEY_FAMILIES):
                    pref = _stable_prefix(node.value)
                    found.setdefault(pref, []).append((rel, node.lineno))
                continue

            # Case 2: f-string (JoinedStr). The first value is often a
            # Constant holding the static prefix, ending with ':'.
            if isinstance(node, ast.JoinedStr) and node.values:
                head = node.values[0]
                if (
                    isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and head.value.startswith(KEY_FAMILIES)
                ):
                    pref = _stable_prefix(head.value)
                    found.setdefault(pref, []).append((rel, node.lineno))
                continue

            # Case 3 (MED-02 closure): walrus operator (NamedExpr) —
            #   `if (key := f"hs:foo:{x}") in ...`
            # The assignment target isn't relevant for prefix extraction;
            # we look at the assigned value directly. ast.walk already
            # visits the inner expression, so this is a belt-and-braces
            # check for clarity; the inner Constant / JoinedStr cases
            # above will trigger first when walk reaches them.
            # Retained here to pin the intention + guard against future
            # AST-walker refactors that might miss walrus inner nodes.
            if isinstance(node, ast.NamedExpr):
                inner = node.value
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value.startswith(KEY_FAMILIES):
                        pref = _stable_prefix(inner.value)
                        found.setdefault(pref, []).append((rel, node.lineno))
                continue

            # Case 4 (MED-02 closure): BinOp string concatenation —
            #   `"hs:" + "foo:" + shop`
            #   `config.REDIS_PREFIX + ":" + key`
            # We walk left-deep to collect contiguous Constant left-
            # operands and build the stable prefix. Stops at the first
            # non-Constant (where the variable part begins).
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                parts: list[str] = []
                cur: ast.AST = node
                # Unwind `((A + B) + C) + D` left-deep.
                while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                    right = cur.right
                    if isinstance(right, ast.Constant) and isinstance(right.value, str):
                        parts.insert(0, right.value)
                    else:
                        parts = []  # non-constant segment — can't confidently extract prefix
                        break
                    cur = cur.left
                if parts and isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                    parts.insert(0, cur.value)
                if parts:
                    joined = "".join(parts)
                    if joined.startswith(KEY_FAMILIES):
                        pref = _stable_prefix(joined)
                        found.setdefault(pref, []).append((rel, node.lineno))

    return found


def main(argv: list[str]) -> int:
    warn_only = "--warn-only" in argv

    if not CLAUDE_MD.exists():
        print(f"audit_claude_md_redis_keys: CLAUDE.md not found — {CLAUDE_MD}")
        return 2
    if not APP_ROOT.exists():
        print(f"audit_claude_md_redis_keys: app/ not found — {APP_ROOT}")
        return 2

    doc_prefixes = _extract_doc_prefixes(CLAUDE_MD.read_text())
    code_prefixes_map = _extract_code_prefixes(APP_ROOT)
    code_prefixes = set(code_prefixes_map.keys())

    # Ignore empty or bare-family prefixes (e.g. raw "hs:" with no sub-
    # namespace — usually a test fixture or a defensive prefix check).
    def _meaningful(p: str) -> bool:
        return p not in {"hs", "llm"} and ":" in p

    doc_prefixes = {p for p in doc_prefixes if _meaningful(p)}
    code_prefixes = {p for p in code_prefixes if _meaningful(p)}

    missing = code_prefixes - doc_prefixes  # used but not documented
    stale = doc_prefixes - code_prefixes    # documented but not used

    if not missing and not stale:
        print(
            f"audit_claude_md_redis_keys: clean — {len(doc_prefixes)} Redis "
            f"key prefixes all documented and in use"
        )
        return 0

    print(
        f"audit_claude_md_redis_keys: DRIFT between app/ and CLAUDE.md §13"
    )
    print()

    if missing:
        print(
            f"  {len(missing)} Redis prefix(es) used in code but NOT in "
            f"CLAUDE.md §13 (add rows with purpose + TTL):"
        )
        for pref in sorted(missing):
            uses = code_prefixes_map.get(pref, [])[:3]
            for p, ln in uses:
                print(f"    + {pref}   ({p}:{ln})")
        print()

    if stale:
        print(
            f"  {len(stale)} Redis prefix(es) in CLAUDE.md but NOT used "
            f"in app/ (remove row, or code was deleted):"
        )
        for pref in sorted(stale):
            print(f"    - {pref}")
        print()

    print(
        "Fix: edit CLAUDE.md §13 — the 'Redis keys — canonical list' table."
    )
    print(
        "Every row in that table backs the 10k-merchant scale invariant "
        "(§12: every key has a TTL). Drift is a scale-correctness bug."
    )

    if warn_only:
        print("\n--warn-only: not failing the audit")
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover
        print(f"audit_claude_md_redis_keys: script error — {exc}", file=sys.stderr)
        sys.exit(2)
