"""
Tests for the elite blocking rules added to reviewer_layer on 2026-04-11.

The 90-day audit showed the reviewer was rubber-stamping: 50 approvals,
0 rejections. The old blocking list was too narrow (TIER_2 + terminal
status + model eval fail). Three new deterministic blockers were added:

  1. Large diff (>200 changed lines) — too risky for auto-approve
  2. Self-modification of the pipeline — defense in depth
  3. Prior failure pattern in same domain (3+ historical failures)

Together these make the reviewer an actual gate, not decoration.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.models.patch_fingerprint import PatchFingerprint
from app.services.reviewer_layer import review_entity


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_candidate(db, *, patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
                   patch_files=None, affected_domain="intelligence",
                   source_type="ops_alert", risk_tier=0,
                   title="test"):
    c = BugFixCandidate(
        source_type=source_type,
        source_ref=f"elite_review_{title}",
        title=title,
        status="patch_proposed",
        patch_diff=patch_diff,
        patch_files=json.dumps(patch_files or ["app/services/foo.py"]),
        patch_risk_tier=risk_tier,
        affected_domain=affected_domain,
    )
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Large-diff blocker
# ---------------------------------------------------------------------------

def test_large_diff_is_blocked(db):
    """A diff with >200 changed lines must fail review."""
    # Build a diff with 250 changed lines
    big_diff_lines = ["--- a/f", "+++ b/f", "@@ -1,250 +1,250 @@"]
    big_diff_lines.extend(f"-line {i}" for i in range(125))
    big_diff_lines.extend(f"+line {i}" for i in range(125))
    big_diff = "\n".join(big_diff_lines) + "\n"

    c = _mk_candidate(
        db,
        patch_diff=big_diff,
        patch_files=["tests/test_something.py"],  # TIER_0 safe path
        title="large_diff_test",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    assert assessment.verdict == "reject", (
        f"expected reject for 250-line diff, got {assessment.verdict}"
    )


def test_normal_size_diff_passes_large_block(db):
    """A 10-line diff must NOT trigger the large-diff blocker."""
    c = _mk_candidate(
        db,
        patch_diff="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n",
        patch_files=["tests/test_ok.py"],
        title="small_diff",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    # Large-diff is not in the reasons
    reasons = json.loads(assessment.blocking_concerns_json or "[]")
    assert not any("Diff too large" in r for r in reasons)


# ---------------------------------------------------------------------------
# Self-modification blocker (defense in depth with classify_patch_risk)
# ---------------------------------------------------------------------------

def test_self_modification_is_blocked_by_reviewer(db):
    """A patch touching the self-healing pipeline is blocked by the
    reviewer even if it somehow reached review with TIER_0."""
    c = _mk_candidate(
        db,
        patch_files=["app/services/bugfix_pipeline.py"],  # self!
        affected_domain="autofix",
        title="self_mod_pipeline",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    reasons = json.loads(assessment.blocking_concerns_json or "[]")
    assert any("self-healing pipeline" in r for r in reasons), (
        f"expected self-healing block, got {reasons}"
    )
    assert assessment.verdict == "reject"


def test_normal_service_passes_self_mod_block(db):
    """A regular service file does NOT trigger the self-modification block."""
    c = _mk_candidate(
        db,
        patch_files=["app/services/nudge_engine.py"],
        affected_domain="nudges",
        title="normal_service",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    reasons = json.loads(assessment.blocking_concerns_json or "[]")
    assert not any("self-healing pipeline" in r for r in reasons)


# ---------------------------------------------------------------------------
# Prior-failure-pattern blocker
# ---------------------------------------------------------------------------

def test_prior_failure_pattern_is_blocked(db):
    """If 3+ historical failures exist for the domain, new candidates are blocked."""
    # Plant 3 historical failures in the 'nudges' domain
    for i in range(3):
        db.add(PatchFingerprint(
            fingerprint=f"fp_prior_fail_{i}",
            bugfix_candidate_id=0,
            outcome="apply_failed",
            failure_reason=f"apply_check_failed: run {i}",
            affected_domain="nudges",
            confidence=1.0,
            created_at=_now() - timedelta(days=10),
        ))
    db.flush()

    c = _mk_candidate(
        db,
        patch_files=["app/services/nudge_engine.py"],
        affected_domain="nudges",
        source_type="ops_alert",
        title="prior_failure_test",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    reasons = json.loads(assessment.blocking_concerns_json or "[]")
    assert any("Prior failure pattern" in r for r in reasons), (
        f"expected prior-failure block, got {reasons}"
    )


def test_two_prior_failures_do_not_block(db):
    """Threshold is 3+ — 2 failures should NOT block yet."""
    for i in range(2):
        db.add(PatchFingerprint(
            fingerprint=f"fp_only_two_{i}",
            bugfix_candidate_id=0,
            outcome="apply_failed",
            failure_reason=f"apply_check_failed: run {i}",
            affected_domain="test_two_fail_dom",
            confidence=1.0,
            created_at=_now() - timedelta(days=10),
        ))
    db.flush()

    c = _mk_candidate(
        db,
        affected_domain="test_two_fail_dom",
        title="two_prior_fails",
    )
    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    reasons = json.loads(assessment.blocking_concerns_json or "[]")
    assert not any("Prior failure pattern" in r for r in reasons)
