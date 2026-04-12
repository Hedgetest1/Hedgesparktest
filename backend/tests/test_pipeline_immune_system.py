"""Tests for D1 — Pipeline immune system.

Contract:
  1. A (source_type, source_ref, skeleton) antigen recorded after a
     regression blocks future candidates that share the same scope and
     skeleton at `_check_patch_fingerprint`.
  2. A different source_ref with the same skeleton is NOT blocked by a
     scoped antigen (though the global skeleton layer may still block).
  3. Antigens with partial keys (missing source_type/ref/skeleton) are
     never written and never matched.
  4. `_record_patch_fingerprint` writes an antigen automatically for
     regression outcomes.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.bugfix_pipeline import (
    _check_antigen,
    _check_patch_fingerprint,
    _compute_antigen_scope_key,
    _record_antigen,
    _record_patch_fingerprint,
)


# ---------- Scope key ----------

def test_scope_key_stable():
    a = _compute_antigen_scope_key("ops_alert", "alert:42")
    b = _compute_antigen_scope_key("ops_alert", "alert:42")
    assert a == b and a is not None


def test_scope_key_distinguishes_source_ref():
    a = _compute_antigen_scope_key("ops_alert", "alert:42")
    b = _compute_antigen_scope_key("ops_alert", "alert:43")
    assert a != b


def test_scope_key_distinguishes_source_type():
    a = _compute_antigen_scope_key("ops_alert", "alert:42")
    b = _compute_antigen_scope_key("sentry_incident", "alert:42")
    assert a != b


def test_scope_key_none_on_missing_field():
    assert _compute_antigen_scope_key(None, "ref") is None
    assert _compute_antigen_scope_key("type", None) is None
    assert _compute_antigen_scope_key("", "ref") is None


# ---------- Record + lookup ----------

def _fresh_scope() -> tuple[str, str]:
    """Return (source_type, source_ref) unique to this test invocation."""
    return "sentry_incident", f"immune_test_{uuid.uuid4().hex[:12]}"


def test_record_and_check_round_trip():
    source_type, source_ref = _fresh_scope()
    scope = _compute_antigen_scope_key(source_type, source_ref)
    skel = f"skel_{uuid.uuid4().hex[:16]}"

    _record_antigen(
        scope_key=scope, skeleton_hash=skel,
        candidate_id=42, outcome="rolled_back",
        failure_reason="regression in subsystem X",
        evidence_source="real_merchant",
    )
    match = _check_antigen(scope, skel)
    if match is None:
        pytest.skip("redis unavailable")
    assert match["candidate_id"] == 42
    assert match["outcome"] == "rolled_back"
    assert match["match_type"] == "immune_antigen"


def test_different_scope_same_skeleton_not_matched():
    """The whole point of scoping: same skeleton, different scope, no block."""
    source_type, ref_a = _fresh_scope()
    _, ref_b = _fresh_scope()
    scope_a = _compute_antigen_scope_key(source_type, ref_a)
    scope_b = _compute_antigen_scope_key(source_type, ref_b)
    skel = f"skel_cross_{uuid.uuid4().hex[:16]}"

    _record_antigen(
        scope_key=scope_a, skeleton_hash=skel,
        candidate_id=1, outcome="rolled_back", failure_reason="",
        evidence_source="real_merchant",
    )
    if _check_antigen(scope_a, skel) is None:
        pytest.skip("redis unavailable")
    assert _check_antigen(scope_b, skel) is None


def test_record_noop_with_partial_keys():
    _record_antigen(scope_key=None, skeleton_hash="x", candidate_id=1, outcome="rolled_back", failure_reason="")
    _record_antigen(scope_key="x", skeleton_hash=None, candidate_id=1, outcome="rolled_back", failure_reason="")
    assert _check_antigen(None, "x") is None
    assert _check_antigen("x", None) is None


# ---------- Integration with _check_patch_fingerprint ----------

def test_check_patch_fingerprint_rejects_via_antigen():
    """When the scope+skeleton antigen fires, _check_patch_fingerprint
    short-circuits with match_type='immune_antigen'."""
    source_type, source_ref = _fresh_scope()
    scope = _compute_antigen_scope_key(source_type, source_ref)
    skel = f"integration_skel_{uuid.uuid4().hex[:16]}"

    _record_antigen(
        scope_key=scope, skeleton_hash=skel,
        candidate_id=777, outcome="rolled_back",
        failure_reason="scope-specific regression",
        evidence_source="real_merchant",
    )
    if _check_antigen(scope, skel) is None:
        pytest.skip("redis unavailable")

    db = MagicMock()
    result = _check_patch_fingerprint(
        db,
        fingerprint=f"unrelated_{uuid.uuid4().hex}",
        diff_fp=f"unrelated_diff_{uuid.uuid4().hex}",
        skeleton_fp=skel,
        source_type=source_type,
        source_ref=source_ref,
    )
    assert result is not None
    assert result["match_type"] == "immune_antigen"
    assert result["candidate_id"] == 777


def test_check_patch_fingerprint_ignores_antigen_when_scope_missing():
    """Without source_type/source_ref, antigen layer is bypassed."""
    skel = f"no_scope_skel_{uuid.uuid4().hex[:16]}"
    db = MagicMock()
    # No source_type/source_ref passed → antigen layer returns None,
    # global skeleton layer also returns None → falls to DB query.
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    result = _check_patch_fingerprint(
        db,
        fingerprint="x",
        diff_fp="y",
        skeleton_fp=skel,
    )
    assert result is None


# ---------- _record_patch_fingerprint writes antigen on regressions ----------

def test_record_patch_fingerprint_writes_antigen_on_rollback(db):
    """On rolled_back outcome, the scoped antigen must be written
    alongside the global skeleton fingerprint — but only when the
    candidate's evidence_source is `real_merchant` (learning isolation)."""
    from app.models.bugfix_candidate import BugFixCandidate

    suffix = uuid.uuid4().hex[:12]
    diff = (
        "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n pass\n"
        f"+immune_test_{suffix}_marker = 1\n"
    )
    c = BugFixCandidate(
        source_type="sentry_incident",
        source_ref=f"record_scope_{suffix}",
        title=f"record antigen {suffix}",
        summary="regression",
        status="rolled_back",
        affected_domain="pipeline",
        patch_diff=diff,
        patch_files='["app/x.py"]',
        evidence_source="real_merchant",
    )
    db.add(c)
    db.flush()

    _record_patch_fingerprint(
        db, c, outcome="rolled_back", failure_reason="regression",
    )

    from app.services.bugfix_pipeline import _compute_ast_skeleton_fingerprint
    skel = _compute_ast_skeleton_fingerprint(diff)
    assert skel is not None
    scope = _compute_antigen_scope_key(c.source_type, c.source_ref)
    match = _check_antigen(scope, skel)
    if match is None:
        pytest.skip("redis unavailable")
    assert match["candidate_id"] == c.id
    assert match["outcome"] == "rolled_back"


def test_antigen_rejected_for_pre_merchant_evidence():
    """Test candidates with pre_merchant evidence must NEVER write
    antigens — otherwise pytest on the prod host (deploy.sh gate)
    would poison the immune system with false positives."""
    source_type, source_ref = _fresh_scope()
    scope = _compute_antigen_scope_key(source_type, source_ref)
    skel = f"pre_merchant_skel_{uuid.uuid4().hex[:16]}"

    _record_antigen(
        scope_key=scope, skeleton_hash=skel,
        candidate_id=1, outcome="rolled_back",
        failure_reason="should not be recorded",
        evidence_source="pre_merchant",
    )
    assert _check_antigen(scope, skel) is None, (
        "pre_merchant regression leaked into the antigen index"
    )


def test_record_patch_fingerprint_no_antigen_on_applied(db):
    """Successful applied outcome must NOT write an antigen (don't poison
    the immune system with valid strategies)."""
    from app.models.bugfix_candidate import BugFixCandidate

    suffix = uuid.uuid4().hex[:12]
    diff = (
        "--- a/y.py\n+++ b/y.py\n@@ -1,1 +1,2 @@\n pass\n"
        f"+success_test_{suffix}_marker = 1\n"
    )
    c = BugFixCandidate(
        source_type="sentry_incident",
        source_ref=f"applied_scope_{suffix}",
        title=f"applied success {suffix}",
        summary="success",
        status="applied",
        affected_domain="pipeline",
        patch_diff=diff,
        patch_files='["app/y.py"]',
    )
    db.add(c)
    db.flush()

    _record_patch_fingerprint(db, c, outcome="applied")

    from app.services.bugfix_pipeline import _compute_ast_skeleton_fingerprint
    skel = _compute_ast_skeleton_fingerprint(diff)
    scope = _compute_antigen_scope_key(c.source_type, c.source_ref)
    assert _check_antigen(scope, skel) is None
