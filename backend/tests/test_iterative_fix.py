"""Test iterative_fix — Sprint C of CTO-brain pipeline upgrade.

Pins:
  * Feature flag off by default
  * should_iterate true iff any finding severity >= threshold AND
    current_iteration < max_depth
  * maybe_schedule_iteration creates child with correct FK,
    iteration_num incremented, source_type='iteration',
    context_json carries parent patch + concerns
  * Max depth reached → escalation ops_alert, no new child
  * No findings / all low-severity → no iteration scheduled
  * Disabled flag → no-op
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models.adversarial_review_finding import AdversarialReviewFinding
from app.models.bugfix_candidate import BugFixCandidate
from app.services import iterative_fix


@pytest.fixture
def enable_iterative(monkeypatch):
    monkeypatch.setenv("ITERATIVE_FIX_ENABLED", "1")
    yield


def _make_parent(db, iteration_num=1, patch_diff="-    bad\n+    good\n"):
    c = BugFixCandidate(
        status="applied",
        source_type="ops_alert",
        source_ref="probe:parent",
        title="parent candidate",
        summary="parent summary",
        patch_diff=patch_diff,
        patch_summary="parent patch summary",
        iteration_num=iteration_num,
    )
    db.add(c)
    db.flush()
    return c


def _make_finding(candidate_id, lens, severity, concern="x", remediation="y"):
    f = AdversarialReviewFinding(
        bugfix_candidate_id=candidate_id,
        lens=lens,
        severity=severity,
        concern=concern,
        suggested_remediation=remediation,
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        tokens_used=100,
    )
    return f


def test_is_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("ITERATIVE_FIX_ENABLED", raising=False)
    assert iterative_fix.is_enabled() is False


def test_is_enabled_on_via_env(monkeypatch):
    monkeypatch.setenv("ITERATIVE_FIX_ENABLED", "1")
    assert iterative_fix.is_enabled() is True


def test_should_iterate_true_on_severity_threshold():
    findings = [
        AdversarialReviewFinding(
            bugfix_candidate_id=1, lens="internal", severity=8),
        AdversarialReviewFinding(
            bugfix_candidate_id=1, lens="investor", severity=3),
    ]
    assert iterative_fix.should_iterate(findings, current_iteration=1) is True


def test_should_iterate_false_when_all_below_threshold():
    findings = [
        AdversarialReviewFinding(
            bugfix_candidate_id=1, lens="internal", severity=5),
        AdversarialReviewFinding(
            bugfix_candidate_id=1, lens="investor", severity=6),
    ]
    assert iterative_fix.should_iterate(findings, current_iteration=1) is False


def test_should_iterate_false_at_max_depth():
    findings = [
        AdversarialReviewFinding(
            bugfix_candidate_id=1, lens="internal", severity=10),
    ]
    assert iterative_fix.should_iterate(findings, current_iteration=3) is False


def test_should_iterate_false_with_no_findings():
    assert iterative_fix.should_iterate([], current_iteration=1) is False


def test_maybe_schedule_disabled_returns_none(db, monkeypatch):
    monkeypatch.delenv("ITERATIVE_FIX_ENABLED", raising=False)
    parent = _make_parent(db)
    findings = [_make_finding(parent.id, "internal", 9)]
    out = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    assert out is None


def test_maybe_schedule_no_blocking_findings(db, enable_iterative):
    parent = _make_parent(db)
    findings = [
        _make_finding(parent.id, "internal", 4),
        _make_finding(parent.id, "investor", 5),
    ]
    out = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    assert out is None


def test_maybe_schedule_creates_iteration_child(db, enable_iterative):
    parent = _make_parent(db, iteration_num=1)
    findings = [
        _make_finding(parent.id, "internal", 8, concern="scale concern",
                      remediation="add index"),
        _make_finding(parent.id, "investor", 3),  # below threshold
    ]
    child = iterative_fix.maybe_schedule_iteration(db, parent, findings)

    assert child is not None
    assert child.parent_candidate_id == parent.id
    assert child.iteration_num == 2
    assert child.source_type == "iteration"
    assert child.status == "open"
    assert child.source_ref == f"iteration:{parent.id}:v2"
    # Context carries parent patch + the blocking finding
    ctx = json.loads(child.context_json)
    assert ctx["iteration_parent_id"] == parent.id
    assert ctx["parent_iteration_num"] == 1
    assert len(ctx["adversarial_concerns"]) == 1
    assert ctx["adversarial_concerns"][0]["severity"] == 8


def test_maybe_schedule_increments_iteration(db, enable_iterative):
    """A parent at iter=2 spawns a child at iter=3."""
    parent = _make_parent(db, iteration_num=2)
    findings = [_make_finding(parent.id, "competitor", 9)]
    child = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    assert child is not None
    assert child.iteration_num == 3


def test_maybe_schedule_escalates_at_max_depth(db, enable_iterative):
    """iter=3 parent + severity 10 → escalation ops_alert, no child."""
    parent = _make_parent(db, iteration_num=3)
    findings = [_make_finding(parent.id, "internal", 10)]
    with patch("app.services.iterative_fix._write_ops_alert") as alert:
        child = iterative_fix.maybe_schedule_iteration(db, parent, findings)

    assert child is None
    # Escalation alert fired
    escalation_calls = [
        c for c in alert.call_args_list
        if "iterative_fix_max_depth_escalation" in str(c)
    ]
    assert len(escalation_calls) == 1


def test_maybe_schedule_respects_max_depth_env(monkeypatch, db, enable_iterative):
    """ITERATIVE_FIX_MAX_DEPTH env overrides default."""
    monkeypatch.setattr(iterative_fix, "_MAX_ITERATION_DEPTH", 2)
    parent = _make_parent(db, iteration_num=2)
    findings = [_make_finding(parent.id, "internal", 10)]
    with patch("app.services.iterative_fix._write_ops_alert"):
        child = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    # depth 2 means stop AT iter 2 (would create iter 3 otherwise)
    assert child is None


def test_propose_patch_augments_prompt_for_iteration_candidate(
    db, enable_iterative, monkeypatch,
):
    """DA Gate-2 closure: the iteration child's context_json is loaded
    by propose_patch and the LLM prompt includes the parent patch +
    blocking concerns. Without this, the loop doesn't converge — the
    LLM would re-propose the same failing patch."""
    # Arrange — create iteration child directly (simulating what
    # maybe_schedule_iteration did in a previous cycle)
    parent = _make_parent(db, patch_diff="-    old_line\n+    new_line\n")
    findings = [_make_finding(parent.id, "internal", 9, concern="X",
                              remediation="Y")]
    child = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    assert child is not None
    assert child.source_type == "iteration"

    # Stub _call_llm to capture the prompt — don't actually invoke LLM
    captured_prompts: list[str] = []

    def _fake_call_llm(user_message, **kwargs):
        captured_prompts.append(user_message)
        return ("", "template_cache", "none")

    # Stub reviewer + telemetry dependencies so propose_patch runs
    from app.services import bugfix_pipeline as bp
    monkeypatch.setattr(bp, "_call_llm", _fake_call_llm)
    # Avoid real LLM budget / network calls
    monkeypatch.setattr(bp, "check_budget_sync", lambda *a, **kw: (True, "ok"),
                        raising=False)

    # Act — call propose_patch on the iteration candidate
    bp.propose_patch(db, child.id)

    # Assert — prompt contains iteration augmentation signals
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "ITERATIVE RE-PROPOSE" in prompt
    assert "Previous patch diff" in prompt
    assert "old_line" in prompt  # parent patch content
    assert "internal" in prompt  # blocking concern lens
    assert "severity 9" in prompt
    assert "remediation hint: Y" in prompt


def test_maybe_schedule_inherits_domain_and_evidence(db, enable_iterative):
    parent = _make_parent(db)
    parent.affected_domain = "revenue_radar"
    parent.evidence_source = "sandbox"
    db.flush()
    findings = [_make_finding(parent.id, "competitor", 8)]
    child = iterative_fix.maybe_schedule_iteration(db, parent, findings)
    assert child.affected_domain == "revenue_radar"
    assert child.evidence_source == "sandbox"
