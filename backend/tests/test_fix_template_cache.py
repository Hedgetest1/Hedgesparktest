"""Tests for D3 — LLM cost amortization via fix-template cache.

Contract:
  1. A successful apply writes a template keyed on
     (affected_domain, source_type, sorted target files).
  2. A subsequent candidate in the same family short-circuits
     `_call_llm` by reusing the cached diff.
  3. The cache key is None when we can't form a stable anchor
     (missing domain, source_type, or file list).
  4. The key is insensitive to file ordering.
  5. The hit counter increments once per reuse.
  6. Reused-template candidates get a context_json annotation.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    _compute_fix_template_key,
    _incr_fix_template_hit,
    _lookup_fix_template,
    _mark_template_reuse,
    _store_fix_template,
    get_fix_template_hits_this_week,
)


# ---------- Key derivation ----------

def _make_candidate(
    *,
    id: int = 1,
    affected_domain: str | None = "pipeline",
    source_type: str | None = "sentry_incident",
    context_json: str | None = None,
    patch_files: str | None = None,
    patch_diff: str | None = None,
    patch_summary: str | None = None,
    test_command: str | None = None,
    title: str = "dummy",
    evidence_source: str | None = "real_merchant",
) -> BugFixCandidate:
    """Default to `real_merchant` so the learning-isolation gate in
    `_store_fix_template` allows the write. Tests that want to
    exercise the rejection side pass an explicit evidence_source."""
    c = BugFixCandidate()
    c.id = id
    c.title = title
    c.affected_domain = affected_domain
    c.source_type = source_type
    c.context_json = context_json
    c.patch_files = patch_files
    c.patch_diff = patch_diff
    c.patch_summary = patch_summary
    c.test_command = test_command
    c.evidence_source = evidence_source
    return c


def test_key_stable_across_file_order():
    a = _make_candidate(patch_files=json.dumps(["app/x.py", "app/y.py"]))
    b = _make_candidate(patch_files=json.dumps(["app/y.py", "app/x.py"]))
    assert _compute_fix_template_key(a) == _compute_fix_template_key(b)
    assert _compute_fix_template_key(a) is not None


def test_key_none_without_domain_or_source_type():
    assert _compute_fix_template_key(
        _make_candidate(affected_domain=None, patch_files='["app/x.py"]')
    ) is None
    assert _compute_fix_template_key(
        _make_candidate(source_type=None, patch_files='["app/x.py"]')
    ) is None


def test_key_none_without_file_anchors():
    """No target_file and no patch_files → no stable key → cache skipped."""
    c = _make_candidate(context_json=json.dumps({"unrelated": 1}))
    assert _compute_fix_template_key(c) is None


def test_key_includes_target_file_from_context():
    c = _make_candidate(
        context_json=json.dumps({"target_file": "app/services/foo.py"}),
    )
    assert _compute_fix_template_key(c) is not None


def test_key_changes_with_domain():
    a = _make_candidate(affected_domain="pipeline", patch_files='["app/x.py"]')
    b = _make_candidate(affected_domain="billing", patch_files='["app/x.py"]')
    assert _compute_fix_template_key(a) != _compute_fix_template_key(b)


# ---------- Store + lookup ----------

def _unique_candidate(patch_diff: str) -> BugFixCandidate:
    """Fresh candidate with a unique file path so each test has its own key."""
    suffix = uuid.uuid4().hex[:10]
    return _make_candidate(
        id=int(uuid.uuid4().int % 10_000_000),
        affected_domain=f"dom_{suffix}",
        source_type="sentry_incident",
        patch_files=json.dumps([f"tests/fake_{suffix}.py"]),
        patch_diff=patch_diff,
        patch_summary="summary " + suffix,
        test_command="pytest tests/fake.py",
    )


def test_store_then_lookup_round_trip():
    c = _unique_candidate("--- a/x\n+++ b/x\n@@\n+print(1)\n")
    key = _compute_fix_template_key(c)
    assert key
    _store_fix_template(key, c)
    cached = _lookup_fix_template(key)
    if cached is None:
        pytest.skip("redis unavailable")
    assert cached["diff"] == c.patch_diff
    assert cached["patch_summary"] == c.patch_summary
    assert cached["source_candidate_id"] == c.id
    assert cached["files"] == json.loads(c.patch_files)


def test_store_is_noop_without_key_or_diff():
    c = _unique_candidate("diff body")
    _store_fix_template(None, c)  # must not raise
    c.patch_diff = None
    _store_fix_template("anykey", c)  # must not raise


def test_lookup_unknown_key_returns_none():
    assert _lookup_fix_template(f"hs_test_unknown_{uuid.uuid4().hex}") is None


def test_lookup_none_key_returns_none():
    assert _lookup_fix_template(None) is None
    assert _lookup_fix_template("") is None


# ---------- Hit counter ----------

def test_hit_counter_increments():
    before = get_fix_template_hits_this_week()
    _incr_fix_template_hit()
    _incr_fix_template_hit()
    after = get_fix_template_hits_this_week()
    if after == before:
        pytest.skip("redis unavailable")
    assert after == before + 2


# ---------- Context annotation ----------

def test_mark_template_reuse_adds_metadata():
    out = _mark_template_reuse(None, source_candidate_id=42)
    data = json.loads(out)
    assert data["fix_template_reuse"]["source_candidate_id"] == 42
    assert "reused_at" in data["fix_template_reuse"]


def test_mark_template_reuse_preserves_existing_context():
    existing = json.dumps({"target_file": "app/x.py", "foo": "bar"})
    out = _mark_template_reuse(existing, source_candidate_id=7)
    data = json.loads(out)
    assert data["target_file"] == "app/x.py"
    assert data["foo"] == "bar"
    assert data["fix_template_reuse"]["source_candidate_id"] == 7


def test_mark_template_reuse_handles_malformed_context():
    out = _mark_template_reuse("not json at all", source_candidate_id=9)
    data = json.loads(out)
    assert data["fix_template_reuse"]["source_candidate_id"] == 9


# ---------- propose_patch integration — the whole point of D3 ----------

def test_propose_patch_short_circuits_on_cache_hit(db):
    """When a fresh cached template matches, propose_patch must skip
    `_call_llm` entirely and populate the candidate from the cache."""
    from unittest.mock import patch as mock_patch

    from app.services.bugfix_pipeline import propose_patch

    suffix = uuid.uuid4().hex[:10]
    test_file = f"tests/template_cache_target_{suffix}.py"

    # Pre-populate the cache with a template keyed on this family+file
    anchor = BugFixCandidate(
        source_type="manual",
        source_ref=f"tmpl_anchor_{suffix}",
        title=f"anchor {suffix}",
        summary="seed",
        status="applied",
        affected_domain=f"dom_{suffix}",
        patch_files=json.dumps([test_file]),
        patch_diff=(
            "--- /dev/null\n"
            f"+++ b/{test_file}\n"
            "@@ -0,0 +1 @@\n"
            "+# cached template\n"
        ),
        patch_summary="cached summary",
        test_command="pytest",
        evidence_source="real_merchant",
    )
    anchor.id = int(uuid.uuid4().int % 10_000_000)
    key = _compute_fix_template_key(anchor)
    assert key is not None
    _store_fix_template(key, anchor)
    if _lookup_fix_template(key) is None:
        pytest.skip("redis unavailable")

    # Create a NEW candidate in the same family — should hit the cache.
    # We use patch_files (not target_file) so preflight's exemption for
    # tests/*.py new files lets us pass without the file needing to exist.
    victim = BugFixCandidate(
        source_type="manual",
        source_ref=f"tmpl_victim_{suffix}",
        title="victim",
        summary="same family, different incident",
        status="open",
        affected_domain=f"dom_{suffix}",
        patch_files=json.dumps([test_file]),
    )
    db.add(victim)
    db.flush()

    hits_before = get_fix_template_hits_this_week()

    with mock_patch(
        "app.services.bugfix_pipeline._call_llm",
        side_effect=AssertionError("LLM must not be called on cache hit"),
    ):
        ok = propose_patch(db, victim.id)

    assert ok is True
    db.refresh(victim)
    assert victim.patch_diff and "cached template" in victim.patch_diff
    assert victim.patch_summary == "cached summary"
    ctx = json.loads(victim.context_json)
    assert ctx["fix_template_reuse"]["source_candidate_id"] == anchor.id
    assert get_fix_template_hits_this_week() == hits_before + 1


def test_store_rejects_non_real_merchant_evidence():
    """Learning-isolation gate: pre_merchant evidence must NEVER enter
    the template cache, even if a test runs on the production host
    (deploy.sh runs pytest pre-deploy and shares the live Redis)."""
    c = _unique_candidate("--- a/x\n+++ b/x\n@@\n+print(42)\n")
    c.evidence_source = "pre_merchant"
    key = _compute_fix_template_key(c)
    assert key
    _store_fix_template(key, c)
    assert _lookup_fix_template(key) is None, (
        "pre_merchant evidence leaked into the template cache"
    )


def test_store_rejects_internal_test_evidence():
    c = _unique_candidate("--- a/x\n+++ b/x\n@@\n+print(1)\n")
    c.evidence_source = "internal_test"
    key = _compute_fix_template_key(c)
    assert key
    _store_fix_template(key, c)
    assert _lookup_fix_template(key) is None


def test_store_rejects_sandbox_evidence():
    c = _unique_candidate("--- a/x\n+++ b/x\n@@\n+print(2)\n")
    c.evidence_source = "sandbox"
    key = _compute_fix_template_key(c)
    assert key
    _store_fix_template(key, c)
    assert _lookup_fix_template(key) is None


def test_propose_patch_calls_llm_on_cache_miss(db):
    """A family with no cached template still goes through `_call_llm`."""
    from unittest.mock import patch as mock_patch

    from app.services.bugfix_pipeline import propose_patch

    suffix = uuid.uuid4().hex[:10]
    test_file = f"tests/template_cache_miss_{suffix}.py"

    victim = BugFixCandidate(
        source_type="manual",
        source_ref=f"tmpl_miss_{suffix}",
        title="miss",
        summary="no cache yet",
        status="open",
        affected_domain=f"dom_miss_{suffix}",
        patch_files=json.dumps([test_file]),
    )
    db.add(victim)
    db.flush()

    mock_response = json.dumps({
        "patch_summary": "fresh llm proposal",
        "files": [test_file],
        "diff": (
            "--- /dev/null\n"
            f"+++ b/{test_file}\n"
            "@@ -0,0 +1 @@\n"
            "+# fresh\n"
        ),
        "test_command": "pytest",
    })

    with mock_patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "anthropic", "claude-sonnet-4-6"),
    ) as mock_llm:
        propose_patch(db, victim.id)
        assert mock_llm.called, "cache miss must fall through to _call_llm"
