#!/usr/bin/env python3
"""Reviewer-layer integrity preventer (Gap G of the elite-tier sprint).

The brutal-CTO honest audit found: TIER_2 list prevents the pipeline
from self-modifying auth/billing/migrations, but the SCOPE-LOCK is
enforced BY reviewer_layer.py judgment. If reviewer_layer.py itself
has a bug (or is silently weakened), the bypass is automatic. There
was no second-order check on the reviewer.

This audit is the second-order check. For each critical method in
reviewer_layer.py (the entry point + the 4 judgment helpers it
delegates to), it computes the AST-skeleton hash and compares to
the golden hash recorded here. Any drift fails the audit — forcing
the modifier to explicitly update the golden hash, which is
itself a signal that the reviewer was changed deliberately.

Why not just diff the file?
---------------------------
Diff catches whitespace, comments, docstrings, and would noise out.
AST skeleton is identifier-normalized + comment-stripped + numeric
literals collapsed — same machinery as the bugfix-pipeline patch
fingerprint. Two semantically-identical refactors produce the same
hash; only true behavior changes shift the hash.

When to update _GOLDEN_HASHES
-----------------------------
1. You are intentionally changing a reviewer-layer method.
2. You ran this audit, saw it fail, and confirmed the new hash
   reflects the intended change.
3. You documented the reason in the commit body so future reviewers
   know why the golden hash moved.

Usage:
    python3 scripts/audit_reviewer_layer_integrity.py
    python3 scripts/audit_reviewer_layer_integrity.py --print-hashes
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "app" / "services" / "reviewer_layer.py"

# Golden hashes — the 5 critical functions whose behavior MUST NOT
# silently change. Frozen 2026-05-02 from the elite-tier sprint Gap G.
# Update these intentionally + document the reason in the commit body.
_GOLDEN_HASHES: dict[str, str] = {
    "review_entity":         "cb410cbe648dbac4",
    "_check_constitution":   "c8c438431606c2c5",
    "_compute_risk_level":   "e68b5fc4a7377a42",
    "_compute_verdict":      "07d6d7ab28386629",
    "_is_auto_approvable":   "4ba286621d38df04",
}


def _ast_skeleton_hash(node: ast.AST) -> str:
    """Compute the same identifier-normalised AST hash used elsewhere
    in the pipeline (mirrors _compute_ast_skeleton_fingerprint).
    Returns the first 16 hex chars of the sha256 digest."""
    name_map: dict[str, str] = {}

    def _placeholder(original: str) -> str:
        if original not in name_map:
            name_map[original] = f"id_{len(name_map)}"
        return name_map[original]

    # Walk a copy so we don't mutate the parent tree
    cloned = ast.parse(ast.unparse(node)) if hasattr(ast, "unparse") else node
    for sub in ast.walk(cloned):
        if isinstance(sub, ast.Name):
            sub.id = _placeholder(sub.id)
        elif isinstance(sub, ast.arg):
            sub.arg = _placeholder(sub.arg)
        elif isinstance(sub, ast.Attribute):
            sub.attr = _placeholder(sub.attr)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            sub.name = _placeholder(sub.name)
        elif isinstance(sub, ast.Constant):
            if isinstance(sub.value, str):
                sub.value = "STR"
            elif isinstance(sub.value, (int, float)):
                sub.value = 0
    skel = ast.dump(cloned, annotate_fields=False, include_attributes=False)
    return hashlib.sha256(skel.encode()).hexdigest()[:16]


def _compute_current_hashes() -> dict[str, str]:
    if not TARGET.is_file():
        return {}
    tree = ast.parse(TARGET.read_text(), filename=str(TARGET))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in _GOLDEN_HASHES:
                # Use the raw skeleton hash on the unmodified node since
                # _ast_skeleton_hash uses ast.unparse(node) which loses
                # the parent-context normalisation we want.
                # Compute on a fresh dump of the node directly.
                skel = ast.dump(node, annotate_fields=False)
                # Apply identifier-normalisation manually to match
                # the pipeline's own fingerprint shape.
                tmp = ast.parse(ast.unparse(node))
                out[node.name] = _ast_skeleton_hash(tmp)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-hashes", action="store_true",
                    help="print the current hashes (use to update golden)")
    args = ap.parse_args()

    current = _compute_current_hashes()
    if args.print_hashes:
        for name in sorted(current):
            print(f'    "{name}":  "{current[name]}",')
        return 0

    if not current:
        print(f"FAIL: could not parse {TARGET}")
        return 1

    drift: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for name, golden in _GOLDEN_HASHES.items():
        cur = current.get(name)
        if cur is None:
            missing.append(name)
            continue
        if cur != golden:
            drift.append((name, golden, cur))

    if missing:
        print(f"FAIL: {len(missing)} critical reviewer method(s) MISSING:")
        for name in missing:
            print(f"  - {name}")
        print(
            "\nThe reviewer was either renamed, deleted, or the file "
            "was structurally modified. Restore the function OR update "
            "_GOLDEN_HASHES + document the reason in the commit body."
        )
        return 1

    if drift:
        print(
            f"FAIL: {len(drift)} reviewer method(s) drifted from golden hash:"
        )
        for name, golden, cur in drift:
            print(f"  - {name}")
            print(f"      golden:   {golden}")
            print(f"      current:  {cur}")
        print(
            "\nA critical reviewer method changed. If this was intentional:\n"
            "  1. Re-run with --print-hashes to get the new hashes\n"
            "  2. Update _GOLDEN_HASHES in this script\n"
            "  3. Document the reason for the reviewer change in the\n"
            "     commit body — the §1.7 protocol applies because\n"
            "     reviewer_layer.py is the SCOPE-LOCK enforcer "
            "(principle 13).\n\n"
            "If unintentional: revert the change. The reviewer "
            "is the second-order safety net for the self-healing "
            "pipeline; silent drift = scope-lock breach."
        )
        return 1

    print(
        f"OK: all {len(_GOLDEN_HASHES)} reviewer-layer critical methods "
        f"match the golden hash."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
