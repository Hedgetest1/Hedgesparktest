"""
Regression guard for the 2026-04-11 "missing_tests false negative" bug.

Before this fix, an evolution candidate that added a test file was judged
ineffective by counting system-wide ops_alerts in a 48h window. System
noise (unrelated alerts spiking) caused alerts_after > alerts_before,
producing false 'ineffective' verdicts on fixes that had actually
achieved their goal.

The fix introduces a goal-scoped evaluator for evolution candidates
(_measure_evolution_goal) that judges the PROPOSAL'S EXPLICIT GOAL
instead of the noise floor of the whole system.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path as _Path

# Derive backend root dynamically for CI portability.
_BACKEND_DIR = str(_Path(os.environ.get("REPO_ROOT", _Path(__file__).parent.parent.parent)) / "backend")

from app.models.bugfix_candidate import BugFixCandidate
from app.services.evolution_outcomes import (
    _measure_evolution_goal,
    _measure_single,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_evo_candidate(db, *, title, patch_files, proposal_type="missing_test"):
    c = BugFixCandidate(
        source_type="evolution",
        source_ref=f"evolution_goal_test_{title[:20]}",
        title=title,
        summary="evolution goal test",
        status="applied",
        applied_at=_now() - timedelta(hours=1),
        patch_files=json.dumps(patch_files),
        context_json=json.dumps({
            "proposal_type": proposal_type,
            "target_file": patch_files[0] if patch_files else None,
        }),
        created_at=_now() - timedelta(hours=2),
    )
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Goal-scoped evaluator
# ---------------------------------------------------------------------------

def test_missing_test_effective_when_file_exists(db):
    """A missing_tests fix is effective if the test file exists on disk."""
    # Use an actual test file that exists in the repo
    existing_test = "tests/test_bugfix_pipeline.py"
    assert os.path.isfile(os.path.join(_BACKEND_DIR, existing_test)), \
        "sanity check — the test file must exist for this test to be meaningful"

    c = _mk_evo_candidate(
        db,
        title="[Evolution] Service foo has no dedicated test file (test_foo)",
        patch_files=[existing_test],
        proposal_type="missing_test_coverage",
    )
    outcome, evidence = _measure_evolution_goal(db, c)
    assert outcome == "effective"
    assert evidence["goal"] == "test_file_exists"
    assert evidence["file_size_bytes"] > 100


def test_missing_test_inconclusive_when_file_absent(db):
    """A missing_tests fix whose test file does NOT exist on disk is
    inconclusive — we cannot prove success or failure."""
    c = _mk_evo_candidate(
        db,
        title="[Evolution] Service bar has no dedicated test (test_bar)",
        patch_files=["tests/test_file_that_definitely_does_not_exist_xyz.py"],
        proposal_type="missing_test_coverage",
    )
    outcome, evidence = _measure_evolution_goal(db, c)
    assert outcome == "inconclusive"
    assert evidence["goal"] == "test_file_missing_post_apply"


def test_unknown_evolution_type_returns_none(db):
    """An evolution candidate whose proposal_type is unknown to the
    goal evaluator returns (None, ...) so the caller can fall back."""
    c = _mk_evo_candidate(
        db,
        title="[Evolution] unknown-goal thing",
        patch_files=["app/services/some_service.py"],
        proposal_type="mystery_refactor",
    )
    outcome, _ = _measure_evolution_goal(db, c)
    assert outcome is None


# ---------------------------------------------------------------------------
# Integration — _measure_single now routes evolution through goal scope
# ---------------------------------------------------------------------------

def test_measure_single_evolution_missing_test_is_not_false_negative(db):
    """
    The regression scenario: an evolution candidate that added a test
    file is measured WITHOUT being penalized by unrelated system-wide
    alert noise.

    Before the fix: _measure_single counted all ops_alerts in the 48h
    window, saw noise, and returned 'ineffective'. Now it routes to
    _measure_evolution_goal first and returns 'effective'.
    """
    existing_test = "tests/test_bugfix_pipeline.py"
    c = _mk_evo_candidate(
        db,
        title="[Evolution] Service xyz has no dedicated test file (test_xyz)",
        patch_files=[existing_test],
        proposal_type="missing_test_coverage",
    )
    outcome, evidence = _measure_single(db, c)
    assert outcome == "effective", (
        f"expected effective (goal-scoped), got {outcome}. Evidence: {evidence}"
    )
    assert evidence["method"] == "goal_scoped"


def test_measure_single_returns_inconclusive_when_unscoped(db):
    """
    Any candidate with NO alert_type scoping AND NO worker scoping
    must return 'inconclusive' — not 'ineffective'. This prevents
    false negatives across the entire system.
    """
    # Create an ops_alert source with a source_ref that won't match any
    # real alert_id (so _extract_alert_type returns None)
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref="alert_9999999999",  # nonexistent
        title="Unscoped test candidate",
        summary="has no scoping",
        status="applied",
        applied_at=_now() - timedelta(hours=1),
        patch_files=None,
        context_json=None,
        created_at=_now() - timedelta(hours=2),
    )
    db.add(c)
    db.flush()

    outcome, evidence = _measure_single(db, c)
    assert outcome == "inconclusive"
    assert evidence.get("note") == "unscoped_measurement_returns_inconclusive"
