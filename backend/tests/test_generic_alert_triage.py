"""Tests for the generic Rule 7 catch-all in run_bug_triage.

Any recurring warning/critical alert that isn't already handled by a
specific rule must become a BugFixCandidate. This closes the gap where
new subsystems write alerts but never get triaged.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.bugfix_candidate import BugFixCandidate
from app.models.ops_alert import OpsAlert
from app.services.bugfix_pipeline import run_bug_triage


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db():
    s: Session = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_alerts(
    db: Session, *, alert_type: str, source: str, count: int,
    severity: str = "warning", shop: str | None = None,
) -> list[OpsAlert]:
    rows: list[OpsAlert] = []
    now = _now()
    for i in range(count):
        a = OpsAlert(
            created_at=now - timedelta(minutes=i),
            severity=severity,
            source=source,
            alert_type=alert_type,
            shop_domain=shop,
            summary=f"{alert_type} occurrence {i}",
            detail=json.dumps({"i": i}),
            resolved=False,
        )
        db.add(a)
        rows.append(a)
    db.flush()
    return rows


def test_generic_recurring_alert_creates_candidate(db):
    """A novel alert_type recurring 3+ times → candidate."""
    unique = f"test_generic_{uuid.uuid4().hex[:8]}"
    _seed_alerts(db, alert_type=unique, source=f"src_{unique}", count=4)

    summary = run_bug_triage(db)

    cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "ops_alert_generic")
        .filter(BugFixCandidate.source_ref.like(f"generic:{unique}:%"))
        .first()
    )
    assert cand is not None, f"expected candidate for {unique}, got summary={summary}"
    assert unique in cand.title


def test_generic_below_threshold_does_not_trigger(db):
    """Only 2 occurrences (threshold=3) → no candidate."""
    unique = f"test_below_{uuid.uuid4().hex[:8]}"
    _seed_alerts(db, alert_type=unique, source=f"src_{unique}", count=2)

    run_bug_triage(db)

    cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like(f"generic:{unique}:%"))
        .first()
    )
    assert cand is None


def test_generic_handled_alert_type_skipped(db):
    """alert_type already covered by Rule 1-6 must NOT be picked by Rule 7."""
    # Hermeticity: frontend_error is a real alert type with prod rows.
    db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "ops_alert_generic",
        BugFixCandidate.source_ref.like("generic:frontend_error:%"),
    ).delete(synchronize_session=False)
    db.flush()
    _seed_alerts(db, alert_type="frontend_error", source="fe:test:abcd1234", count=5)
    run_bug_triage(db)
    # Rule 5 (frontend_error) creates a candidate with source_type='frontend_error',
    # not 'ops_alert_generic'. Rule 7 must NOT also create a duplicate.
    generic_dup = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "ops_alert_generic")
        .filter(BugFixCandidate.source_ref.like("generic:frontend_error:%"))
        .first()
    )
    assert generic_dup is None


def test_generic_info_severity_ignored(db):
    """severity=info is below the noise floor — never triaged."""
    unique = f"test_info_{uuid.uuid4().hex[:8]}"
    _seed_alerts(
        db, alert_type=unique, source=f"src_{unique}", count=5, severity="info",
    )
    run_bug_triage(db)
    cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like(f"generic:{unique}:%"))
        .first()
    )
    assert cand is None


def test_pipeline_self_reference_guard_blocks_bugfix_apply_failed(db):
    """
    A flood of pipeline-internal `bugfix_apply_failed` alerts must NEVER
    spawn a candidate — doing so creates the fix-the-fixer loop that
    caused the 180-message Telegram spam on 2026-04-16.

    This test locks in the exclusion at two layers:
      1. Rule 7's `handled_alert_types` tuple (SQL-level exclusion)
      2. `_create_candidate`'s `_is_pipeline_self_reference` guard
    """
    # Hermeticity: bugfix_apply_failed is emitted by real pipeline flow;
    # prod may have candidates from pre-guard rows.
    db.query(BugFixCandidate).filter(
        BugFixCandidate.source_ref.like("generic:bugfix_apply_failed:%")
    ).delete(synchronize_session=False)
    db.flush()
    _seed_alerts(
        db, alert_type="bugfix_apply_failed",
        source="bugfix_apply", count=10, severity="critical",
    )
    run_bug_triage(db)
    loop_cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like("generic:bugfix_apply_failed:%"))
        .first()
    )
    assert loop_cand is None


def test_pipeline_self_reference_guard_blocks_deploy_failed(db):
    """`deploy_failed` alerts emitted by promotion_pipeline must not loop."""
    db.query(BugFixCandidate).filter(
        BugFixCandidate.source_ref.like("generic:deploy_failed:%")
    ).delete(synchronize_session=False)
    db.flush()
    _seed_alerts(
        db, alert_type="deploy_failed",
        source="deploy:ci_remote", count=10, severity="critical",
    )
    run_bug_triage(db)
    loop_cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like("generic:deploy_failed:%"))
        .first()
    )
    assert loop_cand is None


def test_pipeline_self_reference_guard_blocks_fleet_wide_deploy_failed(db):
    """
    Rule 8 (fleet-wide) consumes alerts that appear across ≥ 3 shops.
    Previously the fleet tuple missed `governed_tier1_applied` and
    `deploy_succeeded` — any new pipeline-internal alert that happens
    to affect ≥ 3 shops would spawn a fleet-wide fix-the-fixer candidate.

    This test seeds the classic fan-out shape for a pipeline-internal
    alert and asserts that NO fleet_wide candidate is created.
    """
    # Hermeticity: governed_tier1_applied is a real alert type.
    db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "fleet_wide",
        BugFixCandidate.source_ref.like("fleet:governed_tier1_applied:%"),
    ).delete(synchronize_session=False)
    db.flush()
    # Seed 4 distinct shops each reporting the same pipeline-internal
    # alert type — fleet threshold is 3, so this would trigger Rule 8
    # if the alert type weren't excluded.
    for shop_idx in range(4):
        shop = f"fleet-test-{shop_idx}.myshopify.com"
        _seed_alerts(
            db, alert_type="governed_tier1_applied",
            source=f"bugfix_apply:candidate_{shop_idx}",
            count=2, severity="warning", shop=shop,
        )
    run_bug_triage(db)
    fleet_cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "fleet_wide")
        .filter(BugFixCandidate.source_ref.like("fleet:governed_tier1_applied:%"))
        .first()
    )
    assert fleet_cand is None, (
        "Rule 8 fleet-wide must not consume pipeline-internal alert types "
        "— that's the exact loop shape that caused the 2026-04-16 spam"
    )


def test_sentry_fingerprint_storm_excluded_from_rule_7(db):
    """
    sentry_fingerprint_storm is a LEADING ops-visibility alert for a
    new fast-firing fingerprint. The underlying sentry_incidents already
    flow into sentry_triage.consume_triage_queue, which creates candidates
    directly from the incident rows. Allowing Rule 7 to also create a
    candidate from the ops_alert yields a duplicate for the same
    underlying bug and wastes LLM budget.

    Lock: 5 recurring storm alerts MUST NOT produce an ops_alert_generic
    candidate.
    """
    # Hermeticity: sentry_fingerprint_storm is emitted by real pipeline.
    db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "ops_alert_generic",
        BugFixCandidate.source_ref.like("generic:sentry_fingerprint_storm:%"),
    ).delete(synchronize_session=False)
    db.flush()
    _seed_alerts(
        db, alert_type="sentry_fingerprint_storm",
        source="sentry_fp_storm:deadbeefcafe1234",
        count=5, severity="critical",
    )
    run_bug_triage(db)
    dup = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "ops_alert_generic")
        .filter(BugFixCandidate.source_ref.like("generic:sentry_fingerprint_storm:%"))
        .first()
    )
    assert dup is None, (
        "sentry_fingerprint_storm must be excluded from Rule 7 so the "
        "underlying sentry_triage fingerprint-level path is the sole "
        "candidate-creating surface for that bug"
    )


def test_rum_regression_routes_through_rule_7(db):
    """
    RUM p75 regression is a new alert class (2026-04-18). It has NO
    parallel triage path — visibility + triage go through Rule 7. Lock
    the contract: 3 recurring rum_regression alerts → ops_alert_generic
    candidate.
    """
    _seed_alerts(
        db, alert_type="rum_regression",
        source="rum:/app",
        count=4, severity="warning",
    )
    run_bug_triage(db)
    cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "ops_alert_generic")
        .filter(BugFixCandidate.source_ref.like("generic:rum_regression:%"))
        .first()
    )
    assert cand is not None, (
        "rum_regression is not pipeline-internal — a recurring run MUST "
        "become a candidate so the self-healing pipeline investigates the "
        "perf drift automatically"
    )


def test_lighthouse_regression_public_routes_through_rule_7(db):
    """
    Same contract as rum_regression but for the public-origin Lighthouse
    class introduced in 2026-04-18. Ensures merchant-observed perf drift
    does not silently accumulate.
    """
    _seed_alerts(
        db, alert_type="lighthouse_regression_public",
        source="lighthouse:public:/app",
        count=4, severity="warning",
    )
    run_bug_triage(db)
    cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "ops_alert_generic")
        .filter(BugFixCandidate.source_ref.like("generic:lighthouse_regression_public:%"))
        .first()
    )
    assert cand is not None


def test_pipeline_self_reference_guard_rejects_candidate_id_context(db):
    """
    Direct unit test of `_is_pipeline_self_reference`: any context that
    references a prior candidate by id must be refused.
    """
    from app.services.bugfix_pipeline import _is_pipeline_self_reference
    assert _is_pipeline_self_reference({"candidate_id": 42}) is not None
    assert _is_pipeline_self_reference({"source_candidate_id": 99}) is not None
    assert _is_pipeline_self_reference({"parent_candidate_id": 7}) is not None
    assert _is_pipeline_self_reference({"alert_type": "gdpr_failure"}) is None
    assert _is_pipeline_self_reference({"alert_type": "bugfix_apply_failed"}) is not None
    assert _is_pipeline_self_reference({"source": "bugfix_apply"}) is not None
    assert _is_pipeline_self_reference({"source": "merchant_chatbot"}) is None
    assert _is_pipeline_self_reference(None) is None
    assert _is_pipeline_self_reference({}) is None
