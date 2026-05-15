"""Contract test: alembic must have exactly ONE head (no branch divergence).

Born 2026-05-15 after the audit-chain sprint discovered TWO alembic
heads silently coexisting:
  - aa7_brain_immutability_hash (applied to prod)
  - zzzg_plan_lite_canonical    (a parallel branch NEVER applied)

`alembic check` validates model-vs-DB drift but does NOT flag branch
divergence. A multi-head state means a fresh DB and prod can land on
DIFFERENT schema depending on which head alembic picks at
`upgrade head`. This is a silent correctness hazard.

The fix is always a merge migration (down_revision = tuple of all
heads). This test makes the divergence un-shippable: it fails the
moment a second head lands, forcing the merge migration in the same
PR rather than discovering the drift weeks later.

Paired with the preflight gate "Alembic single-head check" so the
invariant is enforced both at commit (preflight) and in CI (pytest).
"""
from __future__ import annotations

import os
import subprocess

import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ALEMBIC_BIN = os.path.join(_BACKEND_ROOT, "venv", "bin", "alembic")


def test_alembic_has_exactly_one_head():
    """`alembic heads` must list exactly one (head) line.

    If this fails: run `./venv/bin/alembic heads` to see the divergent
    heads, then add a merge migration whose down_revision is a tuple of
    ALL listed head revision IDs (see migrations/zzzh_audit_chain_anchor.py
    for the canonical example — it merged aa7 + zzzg)."""
    if not os.path.exists(_ALEMBIC_BIN):
        pytest.skip("alembic binary not found in venv")

    result = subprocess.run(
        [_ALEMBIC_BIN, "heads"],
        cwd=_BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # alembic prints INFO logging to stderr; the head list is on stdout.
    head_lines = [
        ln for ln in result.stdout.splitlines() if "(head)" in ln
    ]
    assert len(head_lines) == 1, (
        f"Expected exactly 1 alembic head, found {len(head_lines)}:\n"
        + "\n".join(head_lines)
        + "\n\nBranch divergence detected. Add a merge migration with "
        "down_revision = tuple of all the above head revision IDs. "
        "Canonical example: migrations/versions/zzzh_audit_chain_anchor.py "
        "(merged aa7_brain_immutability_hash + zzzg_plan_lite_canonical)."
    )
