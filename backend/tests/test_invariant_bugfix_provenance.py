"""Runtime invariant: BugFixCandidate.proposal_provider must be populated
whenever propose_patch ran against the candidate.

This test pins the invariant behavior added on 2026-04-23 after an E2E
probe exposed a latent observability gap (see
app/services/invariant_monitor.py::_check_bugfix_proposal_provenance for
full context).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.services.invariant_monitor import _check_bugfix_proposal_provenance


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_provenance_check_fires_when_provider_missing(db):
    """Recent candidate with proposal_attempted_at set but no provider → alert."""
    c = BugFixCandidate(
        source_type="manual",
        source_ref="invariant-provenance-test-1",
        title="Provenance-gap regression probe",
        summary="Synthetic: provider NULL despite attempted_at set",
        status="analyzed",
        proposal_attempted_at=_now() - timedelta(hours=2),
        proposal_provider=None,
        failure_reason="llm_returned_empty_diff",
    )
    db.add(c)
    db.flush()

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_bugfix_proposal_provenance(db, summary)

    assert summary["checked"] >= 1
    assert summary["failed"] >= 1, (
        "invariant must flag orphan-provenance candidate"
    )


def test_provenance_check_green_when_provider_populated(db):
    """Recent candidate WITH proposal_provider set → no alert from this row."""
    # Purge any pre-existing in-window orphans so the assertion below
    # reflects only this row's contribution.
    from sqlalchemy import text
    db.execute(
        text(
            "UPDATE bugfix_candidates SET proposal_provider = 'backfill_test' "
            "WHERE proposal_attempted_at IS NOT NULL "
            "  AND proposal_provider IS NULL "
            "  AND proposal_attempted_at > NOW() - INTERVAL '24 hours'"
        )
    )

    c = BugFixCandidate(
        source_type="manual",
        source_ref="invariant-provenance-test-2",
        title="Provenance-present happy path",
        status="patch_proposed",
        proposal_attempted_at=_now() - timedelta(hours=1),
        proposal_provider="anthropic",
    )
    db.add(c)
    db.flush()

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_bugfix_proposal_provenance(db, summary)

    assert summary["checked"] >= 1
    assert summary["failed"] == 0, (
        "invariant must not flag when provenance is populated"
    )


def test_provenance_check_accepts_template_cache_sentinel(db):
    """Cache-hit path uses 'template_cache' sentinel — legitimate provenance."""
    from sqlalchemy import text
    db.execute(
        text(
            "UPDATE bugfix_candidates SET proposal_provider = 'backfill_test2' "
            "WHERE proposal_attempted_at IS NOT NULL "
            "  AND proposal_provider IS NULL "
            "  AND proposal_attempted_at > NOW() - INTERVAL '24 hours'"
        )
    )

    c = BugFixCandidate(
        source_type="manual",
        source_ref="invariant-provenance-test-3",
        title="Template cache hit provenance",
        status="patch_proposed",
        proposal_attempted_at=_now() - timedelta(minutes=30),
        proposal_provider="template_cache",
    )
    db.add(c)
    db.flush()

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_bugfix_proposal_provenance(db, summary)

    assert summary["failed"] == 0, (
        "'template_cache' sentinel must satisfy the invariant"
    )


def test_provenance_check_ignores_old_rows(db):
    """Candidates older than window must not trigger — 24h rolling.

    Legacy pre-fix rows exist in the DB from before 2026-04-23; the
    invariant uses a 24h window so the alert self-clears once legacy
    rows age out.
    """
    from sqlalchemy import text
    # Purge in-window orphans to isolate this test's contribution.
    db.execute(
        text(
            "UPDATE bugfix_candidates SET proposal_provider = 'backfill_test3' "
            "WHERE proposal_attempted_at IS NOT NULL "
            "  AND proposal_provider IS NULL "
            "  AND proposal_attempted_at > NOW() - INTERVAL '24 hours'"
        )
    )

    c = BugFixCandidate(
        source_type="manual",
        source_ref="invariant-provenance-test-4",
        title="Legacy pre-fix row (older than window)",
        status="analyzed",
        proposal_attempted_at=_now() - timedelta(hours=30),
        proposal_provider=None,
    )
    db.add(c)
    db.flush()

    summary = {"checked": 0, "failed": 0, "alerts_written": 0}
    _check_bugfix_proposal_provenance(db, summary)

    assert summary["failed"] == 0, (
        "rows older than the configured window must not trigger"
    )
