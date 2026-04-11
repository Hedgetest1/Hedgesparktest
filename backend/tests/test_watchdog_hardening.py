"""
Tests for the Phase-5 stuck-state watchdog + graduated thrash score.

These harden the self-healing loop against two previously-undetected failure
modes:

1. Candidates rotting in 'open'/'analyzed'/'patch_proposed' for days without
   any alert firing → the new _recover_stuck_candidates sweep now escalates
   via ops_alert.

2. Aggressive binary thrash suppression hiding real new bugs on
   historically-flaky sources → the graduated thrash_score() returns a
   continuous [0,1] signal that lets the triage pipeline behave more
   intelligently.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.models.ops_alert import OpsAlert
from app.services.bugfix_pipeline import _recover_stuck_candidates
from app.services.loop_health import (
    _THRASH_THRESHOLD,
    is_source_thrashing,
    thrash_score,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_candidate(db, *, status="apply_failed", source_ref="gs_src",
                  days_ago=1, outcome=None) -> BugFixCandidate:
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref=source_ref,
        title="watchdog test",
        status=status,
        created_at=_now() - timedelta(days=days_ago),
    )
    if outcome:
        c.outcome_status = outcome
        c.outcome_measured_at = _now() - timedelta(days=max(days_ago - 1, 0))
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Graduated thrash score
# ---------------------------------------------------------------------------

def test_thrash_score_zero_for_clean_source(db):
    assert thrash_score(db, "ops_alert", "thrash_clean") == 0.0


def test_thrash_score_partial_below_half(db):
    """2 failures is under the suppress cutoff but above zero — warning zone."""
    _mk_candidate(db, source_ref="thrash_partial", status="apply_failed", days_ago=3)
    _mk_candidate(db, source_ref="thrash_partial", status="apply_failed", days_ago=1)
    score = thrash_score(db, "ops_alert", "thrash_partial")
    assert 0.0 < score < 0.5
    # Binary gate should remain False — backward compat preserved
    assert is_source_thrashing(db, "ops_alert", "thrash_partial") is False


def test_thrash_score_at_old_threshold_reaches_half(db):
    """At the legacy binary threshold, the graduated score crosses 0.5 exactly."""
    for i in range(_THRASH_THRESHOLD):
        _mk_candidate(db, source_ref="thrash_threshold", status="apply_failed", days_ago=i + 1)
    score = thrash_score(db, "ops_alert", "thrash_threshold")
    assert score >= 0.5
    # Binary gate also fires here — both systems converge at the cutoff
    assert is_source_thrashing(db, "ops_alert", "thrash_threshold") is True


def test_reopen_from_ineffective_uses_graduated_score(db):
    """Regression for T1.2: reopen_from_ineffective must use thrash_score,
    not the binary is_source_thrashing. A source with 1 prior fail
    (score 0.17) should still be reopenable — the old binary gate only
    blocked at 3+, the graduated gate blocks at score >= 0.5."""
    from app.services.loop_health import reopen_from_ineffective

    # 1 prior fail → score 1/6 = 0.17 → below 0.5 → should reopen
    _mk_candidate(
        db, source_ref="reopen_mild_thrash",
        status="apply_failed", days_ago=10,
    )
    c = _mk_candidate(
        db, source_ref="reopen_mild_thrash",
        status="applied", outcome="ineffective", days_ago=5,
    )
    c.outcome_measured_at = _now() - timedelta(hours=49)
    db.flush()

    result = reopen_from_ineffective(db)
    assert result["reopened"] >= 1, (
        "mild-thrash source (score < 0.5) should still be reopened"
    )


def test_reopen_from_ineffective_suppresses_high_score(db):
    """A source with 3+ prior fails (score >= 0.5) must be suppressed."""
    from app.services.loop_health import reopen_from_ineffective

    # 3 prior apply_failed → score 3/6 = 0.5 → suppressed
    for i in range(3):
        _mk_candidate(
            db, source_ref="reopen_high_thrash",
            status="apply_failed", days_ago=10 + i,
        )
    c = _mk_candidate(
        db, source_ref="reopen_high_thrash",
        status="applied", outcome="ineffective", days_ago=5,
    )
    c.outcome_measured_at = _now() - timedelta(hours=49)
    db.flush()

    result = reopen_from_ineffective(db)
    assert result["suppressed"] >= 1, "high-thrash source must be suppressed"


def test_reopen_passes_thrash_score_in_context(db):
    """The followup candidate's context_json must carry the thrash score
    so propose_patch can use it for prompt enrichment."""
    import json as _json
    from app.services.loop_health import reopen_from_ineffective

    _mk_candidate(
        db, source_ref="reopen_ctx_score",
        status="apply_failed", days_ago=10,
    )
    c = _mk_candidate(
        db, source_ref="reopen_ctx_score",
        status="applied", outcome="ineffective", days_ago=5,
    )
    c.outcome_measured_at = _now() - timedelta(hours=49)
    db.flush()

    reopen_from_ineffective(db)

    followup = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "recurrence",
            BugFixCandidate.source_ref == f"reopen_ops_alert_reopen_ctx_score_{c.id}",
        )
        .first()
    )
    assert followup is not None
    ctx = _json.loads(followup.context_json)
    assert "thrash_score_at_reopen" in ctx
    assert 0.0 < ctx["thrash_score_at_reopen"] < 0.5


def test_thrash_score_caps_at_one(db):
    """Score is bounded — even with many failures it never exceeds 1.0."""
    for i in range(20):
        _mk_candidate(db, source_ref="thrash_saturated", status="apply_failed", days_ago=i + 1)
    assert thrash_score(db, "ops_alert", "thrash_saturated") == 1.0


# ---------------------------------------------------------------------------
# Stuck-state watchdog
# ---------------------------------------------------------------------------

def test_recover_stuck_open_escalates_via_alert(db):
    """Candidates stuck in 'open' for >72h raise a pipeline_stall_open alert."""
    _mk_candidate(
        db,
        source_ref="stuck_open_src",
        status="open",
        days_ago=4,  # > 72h threshold
    )
    _recover_stuck_candidates(db)
    db.flush()

    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "pipeline_stall_open",
            OpsAlert.source == "bugfix_pipeline:stuck:open",
        )
        .first()
    )
    assert alert is not None
    assert alert.severity == "warning"
    assert "open" in alert.summary


def test_recover_stuck_analyzed_escalates_via_alert(db):
    _mk_candidate(
        db,
        source_ref="stuck_analyzed_src",
        status="analyzed",
        days_ago=3,  # > 48h threshold
    )
    _recover_stuck_candidates(db)
    db.flush()

    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "pipeline_stall_analyzed",
            OpsAlert.source == "bugfix_pipeline:stuck:analyzed",
        )
        .first()
    )
    assert alert is not None


def test_recover_stuck_patch_proposed_escalates_via_alert(db):
    _mk_candidate(
        db,
        source_ref="stuck_proposed_src",
        status="patch_proposed",
        days_ago=8,  # > 168h threshold
    )
    _recover_stuck_candidates(db)
    db.flush()

    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "pipeline_stall_proposed",
            OpsAlert.source == "bugfix_pipeline:stuck:patch_proposed",
        )
        .first()
    )
    assert alert is not None


def test_recover_stuck_no_alert_below_threshold(db):
    """A fresh 'open' candidate does not trigger an alert."""
    # Clean any historical alerts for this source before we start
    db.query(OpsAlert).filter(
        OpsAlert.source == "bugfix_pipeline:stuck:open",
        OpsAlert.resolved == False,
    ).delete(synchronize_session=False)
    db.flush()

    _mk_candidate(db, source_ref="fresh_src", status="open", days_ago=1)
    _recover_stuck_candidates(db)
    db.flush()

    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "pipeline_stall_open",
            OpsAlert.source == "bugfix_pipeline:stuck:open",
            OpsAlert.resolved == False,
        )
        .first()
    )
    assert alert is None


def test_recover_stuck_dedups_repeated_escalation(db):
    """Running the watchdog twice in quick succession produces only one alert."""
    _mk_candidate(db, source_ref="dedup_escalate", status="open", days_ago=5)
    _recover_stuck_candidates(db)
    db.flush()
    _recover_stuck_candidates(db)
    db.flush()

    count = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "pipeline_stall_open",
            OpsAlert.source == "bugfix_pipeline:stuck:open",
            OpsAlert.resolved == False,
        )
        .count()
    )
    # There may be pre-existing alerts for this source from earlier sweeps
    # on the shared dev DB — the invariant we care about is "≤ 1 unresolved
    # alert per (source, type)".
    assert count <= 1


# ---------------------------------------------------------------------------
# Dedup hardening — 24h window covers terminal states
# ---------------------------------------------------------------------------

class TestDedupHardening:
    def test_discarded_candidate_within_24h_blocks_new_triage(self, db):
        """Regression for merchant_bug_alert_16985 prod pattern: an alert
        triaged → discarded at T0, then triaged again at T+15min, used to
        create a second duplicate discarded candidate. The 24h window
        now blocks the second triage."""
        from app.services.bugfix_pipeline import _has_open_candidate

        _mk_candidate(
            db,
            source_ref="dedup_recent_discard",
            status="discarded",
            days_ago=0,
        )
        assert _has_open_candidate(db, "ops_alert", "dedup_recent_discard") is True

    def test_apply_failed_within_24h_blocks_new_triage(self, db):
        """A fresh apply_failed should not be re-triaged the next cycle.
        Thrash_score handles the longer-term retry decision."""
        from app.services.bugfix_pipeline import _has_open_candidate

        _mk_candidate(
            db,
            source_ref="dedup_recent_fail",
            status="apply_failed",
            days_ago=0,
        )
        assert _has_open_candidate(db, "ops_alert", "dedup_recent_fail") is True

    def test_applied_within_24h_blocks_while_outcome_pending(self, db):
        """An applied fix whose outcome has not yet been measured must not
        be re-triaged — we're still measuring effectiveness."""
        from app.services.bugfix_pipeline import _has_open_candidate

        _mk_candidate(
            db,
            source_ref="dedup_recent_applied",
            status="applied",
            days_ago=0,
        )
        assert _has_open_candidate(db, "ops_alert", "dedup_recent_applied") is True

    def test_discarded_older_than_24h_does_not_block(self, db):
        """After the 24h window opens again, thrash_score takes over."""
        from app.services.bugfix_pipeline import _has_open_candidate

        _mk_candidate(
            db,
            source_ref="dedup_old_discard",
            status="discarded",
            days_ago=3,  # > 24h
        )
        assert _has_open_candidate(db, "ops_alert", "dedup_old_discard") is False


def test_recover_stuck_preserves_applying_reset_behavior(db):
    """The original applying → patch_proposed recovery still works."""
    c = BugFixCandidate(
        source_type="ops_alert",
        source_ref="still_recovers",
        title="applying recovery",
        status="applying",
        decided_at=_now() - timedelta(minutes=15),
        created_at=_now() - timedelta(hours=1),
    )
    db.add(c)
    db.flush()

    _recover_stuck_candidates(db)
    db.refresh(c)
    assert c.status == "patch_proposed"
    assert c.failure_reason == "stuck_in_applying_recovered"
