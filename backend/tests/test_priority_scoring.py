"""
Tests for the deterministic priority-scoring layer added 2026-04-11.

Before this module, bugfix_candidates.priority_score was declared but
never populated. Audit showed 85/86 rows with NULL priority, causing
`ORDER BY priority_score DESC NULLS LAST` to degenerate to FIFO. A
critical webhook bug could sit behind a stale evolution proposal
simply because the latter was older.

These tests prove the scoring function is:
  1. Deterministic — same inputs always produce the same score
  2. Correctly weighted — criticals outrank warnings, criticals outrank lows
  3. Recency-aware — fresh incidents rank higher
  4. Recurrence-aware — repeat failures rank higher
  5. Integrated into _create_candidate so every new row has a score
  6. Refined post-classification via recompute_priority_after_classification
  7. Backfillable for historical NULL rows
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    backfill_priority_scores,
    compute_priority_score,
    recompute_priority_after_classification,
    _create_candidate,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_score_is_deterministic():
    """Same inputs → same score, every time."""
    args = dict(
        severity="critical",
        source_type="ops_alert",
        affected_domain_criticality="critical",
        recurrence_count=2,
        age_minutes=10,
    )
    s1, _ = compute_priority_score(**args)
    s2, _ = compute_priority_score(**args)
    s3, _ = compute_priority_score(**args)
    assert s1 == s2 == s3


def test_score_in_bounds():
    """All inputs produce a score in [0, 100]."""
    for sev in ("critical", "warning", "info", None):
        for src in ("sentry_incident", "ops_alert", "evolution", "manual", None):
            for crit in ("critical", "high", "medium", "low", None):
                for rec in (0, 1, 3, 10):
                    for age in (0, 120, 1000, 100000):
                        s, _ = compute_priority_score(
                            severity=sev,
                            source_type=src,
                            affected_domain_criticality=crit,
                            recurrence_count=rec,
                            age_minutes=age,
                        )
                        assert 0 <= s <= 100, f"score {s} out of bounds for {(sev, src, crit, rec, age)}"


# ---------------------------------------------------------------------------
# Correctness — the ordering invariants that matter for the queue
# ---------------------------------------------------------------------------

def test_critical_webhook_outranks_stale_evolution():
    """The exact prod scenario: a critical webhook bug must outrank a
    stale low-criticality evolution proposal."""
    critical_webhook, _ = compute_priority_score(
        severity="critical",
        source_type="ops_alert",
        affected_domain_criticality="critical",
        recurrence_count=1,
        age_minutes=30,
    )
    stale_evolution, _ = compute_priority_score(
        severity="info",
        source_type="evolution",
        affected_domain_criticality="low",
        recurrence_count=0,
        age_minutes=10_000,  # old
    )
    assert critical_webhook > stale_evolution + 20, (
        f"critical webhook ({critical_webhook}) barely outranks stale evolution "
        f"({stale_evolution}); weights are too flat"
    )


def test_sentry_incident_outranks_frontend_error():
    """Backend Sentry incidents (runtime errors) outrank frontend UI errors."""
    sentry, _ = compute_priority_score(
        severity="critical", source_type="sentry_incident",
        affected_domain_criticality="high", recurrence_count=0, age_minutes=5,
    )
    fe, _ = compute_priority_score(
        severity="warning", source_type="frontend_error",
        affected_domain_criticality="medium", recurrence_count=0, age_minutes=5,
    )
    assert sentry > fe


def test_recurrence_boosts_priority():
    """A candidate that has recurred must outrank the same source first-time."""
    first_time, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="medium", recurrence_count=0, age_minutes=10,
    )
    recurred, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="medium", recurrence_count=5, age_minutes=10,
    )
    assert recurred > first_time


def test_fresh_outranks_old():
    fresh, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="medium", recurrence_count=1, age_minutes=10,
    )
    old, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="medium", recurrence_count=1, age_minutes=10_000,
    )
    assert fresh > old


def test_critical_domain_outranks_low_domain_same_severity():
    """Holding severity constant, a critical domain must win."""
    critical_dom, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="critical", recurrence_count=0, age_minutes=10,
    )
    low_dom, _ = compute_priority_score(
        severity="warning", source_type="ops_alert",
        affected_domain_criticality="low", recurrence_count=0, age_minutes=10,
    )
    assert critical_dom > low_dom


# ---------------------------------------------------------------------------
# Integration — _create_candidate writes a score
# ---------------------------------------------------------------------------

def test_create_candidate_populates_priority_score(db):
    """Every candidate created by the triage path has a non-null score."""
    c = _create_candidate(
        db,
        source_type="ops_alert",
        source_ref="priority_test_1",
        title="Priority integration test",
        summary_text="Should have a priority",
        context={"detail": {"severity": "critical"}, "recurrence_count": 3},
    )
    assert c.priority_score is not None
    assert 0 <= c.priority_score <= 100
    # Critical severity + recurrence 3 should yield a reasonably high score
    assert c.priority_score >= 45

    # context carries the breakdown for ops explainability
    ctx = json.loads(c.context_json)
    assert "priority_breakdown" in ctx
    assert set(ctx["priority_breakdown"].keys()) == {
        "severity", "criticality", "recency", "recurrence", "source_type",
    }


def test_recompute_after_classification_updates_score(db):
    """Once affected_domain is set, priority must be refined."""
    c = _create_candidate(
        db,
        source_type="ops_alert",
        source_ref="priority_recompute_test",
        title="Domain lift test",
        summary_text="becomes critical after classification",
        context={"detail": {"severity": "warning"}},
    )
    initial_score = c.priority_score
    assert initial_score is not None

    # Simulate classification finding that this touches webhooks (critical)
    c.affected_domain = "webhooks"
    recompute_priority_after_classification(c)
    assert c.priority_score > initial_score, (
        f"after classifying as webhooks (critical), score should have increased "
        f"from {initial_score} but is {c.priority_score}"
    )

    # Breakdown must now include the domain criticality lift
    ctx = json.loads(c.context_json)
    assert ctx.get("priority_domain_criticality") == "critical"


# ---------------------------------------------------------------------------
# Backfill — historical NULL rows
# ---------------------------------------------------------------------------

def test_backfill_populates_null_rows(db):
    """Rows created without a score (historical rows) get filled."""
    # Create candidates with priority_score explicitly NULL to simulate history
    c1 = BugFixCandidate(
        source_type="ops_alert",
        source_ref="backfill_test_1",
        title="old row 1",
        status="open",
        priority_score=None,
        created_at=_now() - timedelta(days=2),
    )
    c2 = BugFixCandidate(
        source_type="evolution",
        source_ref="backfill_test_2",
        title="old row 2",
        status="discarded",
        priority_score=None,
        created_at=_now() - timedelta(days=10),
    )
    db.add_all([c1, c2])
    db.flush()

    before_null = (
        db.query(BugFixCandidate)
        .filter(
            (BugFixCandidate.priority_score.is_(None))
            | (BugFixCandidate.priority_score == 0),
            BugFixCandidate.source_ref.like("backfill_test_%"),
        )
        .count()
    )
    assert before_null >= 2

    summary = backfill_priority_scores(db, limit=50)
    assert summary["scanned"] >= 2
    assert summary["backfilled"] >= 2

    db.refresh(c1)
    db.refresh(c2)
    assert c1.priority_score is not None and c1.priority_score > 0
    assert c2.priority_score is not None and c2.priority_score > 0


def test_backfill_does_not_overwrite_existing_scores(db):
    """A row with a non-null score must NOT be modified by backfill."""
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref="backfill_preserve_test",
        title="already scored",
        status="open",
        priority_score=77,
        created_at=_now(),
    )
    db.add(c)
    db.flush()

    backfill_priority_scores(db, limit=500)
    db.refresh(c)
    assert c.priority_score == 77, (
        "backfill must not touch already-scored rows — observability stability"
    )
