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
