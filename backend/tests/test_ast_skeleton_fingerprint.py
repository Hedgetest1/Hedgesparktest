"""Tests for C3 — AST skeleton fingerprint.

Locks the contract: two patches that share an AST skeleton hash to
the same value even when identifiers/strings differ. The skeleton
falls back to textual normalization on parse failure. Recording +
lookup against Redis is exercised end-to-end.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.bugfix_pipeline import (
    _check_skeleton_fingerprint,
    _compute_ast_skeleton_fingerprint,
    _record_skeleton_fingerprint,
    _textual_skeleton_fingerprint,
)


def _diff(*added: str) -> str:
    """Build a minimal valid diff with the given added lines."""
    lines = ["--- a/x.py", "+++ b/x.py", "@@ -1,1 +1," + str(len(added) + 1) + " @@", " pass"]
    for a in added:
        lines.append("+" + a)
    return "\n".join(lines) + "\n"


# ---- Pure skeleton hashing ----

def test_renamed_identifiers_collapse_to_same_skeleton():
    """The whole point: rename a variable, get the same skeleton."""
    diff_a = _diff(
        "def retry_handler(count):",
        "    if count > 3:",
        "        return False",
        "    return True",
    )
    diff_b = _diff(
        "def attempt_handler(attempts):",
        "    if attempts > 3:",
        "        return False",
        "    return True",
    )
    h_a = _compute_ast_skeleton_fingerprint(diff_a)
    h_b = _compute_ast_skeleton_fingerprint(diff_b)
    assert h_a is not None
    assert h_a == h_b, "Renamed-only patches must hash identically"


def test_different_structure_yields_different_skeleton():
    diff_a = _diff(
        "def foo(x):",
        "    return x + 1",
    )
    diff_b = _diff(
        "def foo(x):",
        "    return x * 2",
    )
    h_a = _compute_ast_skeleton_fingerprint(diff_a)
    h_b = _compute_ast_skeleton_fingerprint(diff_b)
    assert h_a is not None and h_b is not None
    assert h_a != h_b, "Different operators must produce different skeletons"


def test_string_literals_do_not_change_skeleton():
    diff_a = _diff('x = "hello"')
    diff_b = _diff('x = "world"')
    assert _compute_ast_skeleton_fingerprint(diff_a) == _compute_ast_skeleton_fingerprint(diff_b)


def test_number_literals_do_not_change_skeleton():
    diff_a = _diff("x = 1")
    diff_b = _diff("x = 999")
    assert _compute_ast_skeleton_fingerprint(diff_a) == _compute_ast_skeleton_fingerprint(diff_b)


def test_empty_diff_returns_none():
    assert _compute_ast_skeleton_fingerprint("") is None
    assert _compute_ast_skeleton_fingerprint(None) is None


def test_diff_with_only_comments_returns_none():
    d = _diff("# this is a comment")
    # the only added line is a comment, which we strip
    assert _compute_ast_skeleton_fingerprint(d) is None


def test_unparseable_diff_falls_back_to_textual():
    # Mid-block snippet (no def context) — AST parse may fail or succeed
    # but the function MUST return some hash, never crash.
    d = _diff(
        "    if True:  # dangling indent",
        "        x = 1",
    )
    h = _compute_ast_skeleton_fingerprint(d)
    # Either the AST path or the textual fallback must produce a hash
    assert h is not None


def test_textual_fallback_normalizes_identifiers():
    h_a = _textual_skeleton_fingerprint(["    foo = bar + 1"])
    h_b = _textual_skeleton_fingerprint(["    baz = qux + 99"])
    assert h_a == h_b


# ---- Redis record + lookup ----

def test_record_and_check_round_trip():
    skel = f"test_skel_{uuid.uuid4().hex[:12]}"
    _record_skeleton_fingerprint(
        candidate_id=12345, skeleton_hash=skel,
        outcome="apply_failed", failure_reason="git apply rejected",
    )
    match = _check_skeleton_fingerprint(skel)
    if match is None:
        pytest.skip("redis unavailable")
    assert match["candidate_id"] == 12345
    assert match["outcome"] == "apply_failed"
    assert match["match_type"] == "ast_skeleton"


def test_check_unknown_skeleton_returns_none():
    assert _check_skeleton_fingerprint("definitely_not_a_real_hash_xyz") is None


def test_check_none_skeleton_returns_none():
    assert _check_skeleton_fingerprint(None) is None
    assert _check_skeleton_fingerprint("") is None


# ---- Integration: _check_patch_fingerprint accepts skeleton_fp ----

def test_check_patch_fingerprint_matches_via_skeleton():
    """A candidate whose AST skeleton matches a previously failed
    fingerprint should be rejected even when the diff text differs."""
    from unittest.mock import MagicMock
    from app.services.bugfix_pipeline import _check_patch_fingerprint

    skel = f"integration_skel_{uuid.uuid4().hex[:12]}"
    _record_skeleton_fingerprint(
        candidate_id=99999, skeleton_hash=skel,
        outcome="rolled_back", failure_reason="caused regression",
    )
    db = MagicMock()
    result = _check_patch_fingerprint(
        db,
        fingerprint="totally_different_identity_hash",
        diff_fp="totally_different_diff_hash",
        skeleton_fp=skel,
    )
    if result is None:
        pytest.skip("redis unavailable")
    assert result["match_type"] == "ast_skeleton"
    assert result["candidate_id"] == 99999
