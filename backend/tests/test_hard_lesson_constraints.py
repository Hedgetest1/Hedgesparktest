"""
Tests for the hard lesson constraints injector (2026-04-11 elite sprint).

Every failed PatchFingerprint in a domain becomes an explicit DO-NOT
rule in the next propose_patch for the same domain. The accumulated
failure catalog is the competitive moat: a fresh deployment starts
empty and learns the hard way; a production deployment passes months
of observed failure modes into every LLM call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.patch_fingerprint import PatchFingerprint
from app.services.bugfix_pipeline import (
    build_hard_lesson_constraints,
    _FAILURE_REASON_TEMPLATES,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _plant_failures(db, domain: str, failures: list[tuple[str, int]]):
    """Plant N PatchFingerprint rows per (failure_reason, count)."""
    for reason, count in failures:
        for i in range(count):
            db.add(PatchFingerprint(
                fingerprint=f"fp_{domain}_{reason}_{i}"[:64],
                bugfix_candidate_id=0,
                outcome="apply_failed",
                failure_reason=reason,
                affected_domain=domain,
                confidence=1.0,
                created_at=_now() - timedelta(days=10),
            ))
    db.flush()


def test_no_failures_returns_none(db):
    """A fresh domain with no history returns None (no injection)."""
    result = build_hard_lesson_constraints(
        db, affected_domain="fresh_dom_xyz", source_type="ops_alert",
    )
    assert result is None


def test_groups_by_failure_family_prefix(db):
    """Failures are grouped by the prefix before the first ':'."""
    _plant_failures(db, "test_dom_1", [
        ("semantic_validation_failed: file_not_found: services/foo.py", 3),
        ("semantic_validation_failed: file_not_found: services/bar.py", 2),
        ("apply_check_failed: corrupt patch at line 88", 2),
    ])
    result = build_hard_lesson_constraints(
        db, affected_domain="test_dom_1", source_type="ops_alert",
    )
    assert result is not None
    # The two semantic failures should collapse into one family with count 5
    assert "semantic_validation_failed" in result
    assert "5x" in result or "[5x]" in result
    assert "apply_check_failed" in result
    assert "2x" in result or "[2x]" in result


def test_injects_human_readable_explanation(db):
    """Known failure families get a human-readable DO-NOT explanation."""
    _plant_failures(db, "test_dom_2", [
        ("llm_returned_empty_diff", 4),
    ])
    result = build_hard_lesson_constraints(
        db, affected_domain="test_dom_2", source_type="ops_alert",
    )
    assert result is not None
    # The explanation from _FAILURE_REASON_TEMPLATES must appear
    expected_snippet = _FAILURE_REASON_TEMPLATES["llm_returned_empty_diff"][:30]
    assert expected_snippet in result


def test_scoped_by_domain(db):
    """Failures in domain A must NOT leak into domain B's prompt."""
    _plant_failures(db, "test_dom_a", [
        ("apply_check_failed: something", 5),
    ])
    # Query for a DIFFERENT domain
    result = build_hard_lesson_constraints(
        db, affected_domain="test_dom_b", source_type="ops_alert",
    )
    assert result is None


def test_respects_lookback_window(db):
    """Old failures (> lookback_days) must not appear in the prompt."""
    # Plant a failure 200 days ago
    db.add(PatchFingerprint(
        fingerprint="fp_old_x",
        bugfix_candidate_id=0,
        outcome="apply_failed",
        failure_reason="apply_check_failed: ancient",
        affected_domain="test_dom_old",
        confidence=1.0,
        created_at=_now() - timedelta(days=200),
    ))
    db.flush()

    result = build_hard_lesson_constraints(
        db, affected_domain="test_dom_old", source_type="ops_alert",
        lookback_days=90,
    )
    assert result is None


def test_caps_at_max_rules(db):
    """Even with 100 distinct failure families, only top N are returned."""
    failures = [(f"family_{i}_custom", 1) for i in range(20)]
    _plant_failures(db, "test_dom_cap", failures)
    result = build_hard_lesson_constraints(
        db, affected_domain="test_dom_cap", source_type="ops_alert",
        max_rules=5,
    )
    assert result is not None
    # Count the bullet lines (exclude header lines)
    bullets = [l for l in result.split("\n") if l.startswith("- ")]
    assert len(bullets) <= 5


def test_returns_none_for_null_domain(db):
    """No domain → no constraints, no crash."""
    assert build_hard_lesson_constraints(
        db, affected_domain=None, source_type="ops_alert",
    ) is None
