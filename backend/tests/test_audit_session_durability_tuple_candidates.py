"""Contract test for the S5/S12 audit-helper tuple evolution.

Born 2026-05-15. The cache-first /dashboard/overview refactor moved
the merchant-existence (S12) + sv-mismatch (S5) gates out of
require_merchant_session into the shared _resolve_session_identity
resolver (single source of truth). audit_session_durability_invariants
helpers were evolved to accept a TUPLE of candidate function names:
pass if ANY contains the gate, fail if ALL lose it — preserving the
prevention value while tracking the legitimate delegation refactor.

This test pins that contract so a future change can't silently
weaken it back to a single-function check (which would either
false-fail the refactored code or miss a real gate removal).
"""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

import scripts.audit_session_durability_invariants as A


def _write(tmp_path: Path, src: str) -> Path:
    p = tmp_path / "sample.py"
    p.write_text(src)
    return p


# --- py_function_has_none_check: tuple semantics ---------------------------

def test_none_check_passes_when_gate_in_first_candidate(tmp_path):
    src = (
        "def a(x):\n"
        "    if merchant is None:\n"
        "        raise RuntimeError('nope')\n"
        "def b(x):\n"
        "    return 1\n"
    )
    ok, msg = A.py_function_has_none_check(_write(tmp_path, src), ("a", "b"), "merchant")
    assert ok is True, msg


def test_none_check_passes_when_gate_only_in_second_candidate(tmp_path):
    """The refactor shape: public wrapper delegates, gate lives in helper."""
    src = (
        "def require_merchant_session(x):\n"
        "    shop, reason = _resolve(x)\n"
        "    return shop\n"
        "def _resolve(x):\n"
        "    if merchant is None:\n"
        "        return None, _SESS_NO_MERCHANT\n"
    )
    ok, msg = A.py_function_has_none_check(
        _write(tmp_path, src), ("require_merchant_session", "_resolve"), "merchant"
    )
    assert ok is True, msg
    assert "sentinel-reason return" in msg or "raise present" in msg


def test_none_check_FAILS_when_gate_absent_from_all_candidates(tmp_path):
    """Prevention preserved: removing the gate from BOTH must fail loudly."""
    src = (
        "def require_merchant_session(x):\n"
        "    return x\n"
        "def _resolve(x):\n"
        "    return x, 'ok'\n"
    )
    ok, msg = A.py_function_has_none_check(
        _write(tmp_path, src), ("require_merchant_session", "_resolve"), "merchant"
    )
    assert ok is False
    assert "no `if merchant is None" in msg


def test_none_check_sentinel_requires_reason_constant(tmp_path):
    """A bare `return None` (no _SESS_ reason) must NOT satisfy the gate —
    only a raise OR a sentinel-reason return counts."""
    src = (
        "def _resolve(x):\n"
        "    if merchant is None:\n"
        "        return None\n"  # no reason constant → not a real gate
    )
    ok, msg = A.py_function_has_none_check(_write(tmp_path, src), ("_resolve",), "merchant")
    assert ok is False, msg


def test_none_check_str_arg_still_works_backcompat(tmp_path):
    """All other S-invariants pass a plain str — must stay supported."""
    src = "def f(x):\n    if merchant is None:\n        raise ValueError()\n"
    ok, _ = A.py_function_has_none_check(_write(tmp_path, src), "f", "merchant")
    assert ok is True


# --- py_function_has_compare: tuple semantics -----------------------------

def test_compare_passes_when_in_second_candidate(tmp_path):
    src = (
        "def require_merchant_session(x):\n"
        "    return _resolve(x)\n"
        "def _resolve(x):\n"
        "    if token_sv < db_sv:\n"
        "        return None, 'sv'\n"
    )
    ok, msg = A.py_function_has_compare(
        _write(tmp_path, src),
        ("require_merchant_session", "_resolve"),
        left="token_sv", op_type=ast.Lt, right="db_sv",
    )
    assert ok is True, msg


def test_compare_FAILS_when_absent_from_all(tmp_path):
    src = (
        "def require_merchant_session(x):\n    return x\n"
        "def _resolve(x):\n    return x\n"
    )
    ok, msg = A.py_function_has_compare(
        _write(tmp_path, src),
        ("require_merchant_session", "_resolve"),
        left="token_sv", op_type=ast.Lt, right="db_sv",
    )
    assert ok is False
    assert "no `token_sv < db_sv`" in msg


def test_compare_str_arg_backcompat(tmp_path):
    src = "def f(x):\n    if token_sv < db_sv:\n        pass\n"
    ok, _ = A.py_function_has_compare(
        _write(tmp_path, src), "f", left="token_sv", op_type=ast.Lt, right="db_sv"
    )
    assert ok is True


def test_live_deps_module_satisfies_s5_s12_post_refactor():
    """End-to-end: the REAL app/core/deps.py must satisfy S5+S12 via the
    delegation pair after the 2026-05-15 cache-first refactor."""
    deps = A.BACKEND / "app/core/deps.py"
    ok_s12, msg12 = A.py_function_has_none_check(
        deps, ("require_merchant_session", "_resolve_session_identity"), "merchant"
    )
    ok_s5, msg5 = A.py_function_has_compare(
        deps, ("require_merchant_session", "_resolve_session_identity"),
        left="token_sv", op_type=ast.Lt, right="db_sv",
    )
    assert ok_s12 is True, f"S12 regressed: {msg12}"
    assert ok_s5 is True, f"S5 regressed: {msg5}"
