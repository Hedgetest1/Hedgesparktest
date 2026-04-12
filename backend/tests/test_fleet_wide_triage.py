"""Tests for B2 — cross-shop pattern compaction (Rule 8)."""
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


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def db():
    s: Session = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _seed_alert(db: Session, *, alert_type: str, source: str, shop: str, severity: str = "warning"):
    a = OpsAlert(
        created_at=_now(),
        severity=severity,
        source=source,
        alert_type=alert_type,
        shop_domain=shop,
        summary=f"{alert_type} for {shop}",
        detail=json.dumps({"shop": shop}),
        resolved=False,
    )
    db.add(a)
    db.flush()
    return a


def test_three_shops_with_same_template_creates_fleet_wide_candidate(db):
    unique = f"fleet_test_{uuid.uuid4().hex[:8]}"
    # Same alert_type, same source TEMPLATE (only the suffix differs)
    for shop_idx in range(3):
        _seed_alert(
            db,
            alert_type=unique,
            source=f"probe:cvr_drift:shop_{shop_idx}",
            shop=f"shop{shop_idx}.myshopify.com",
        )

    summary = run_bug_triage(db)

    fleet_cand = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "fleet_wide",
            BugFixCandidate.source_ref.like(f"fleet:{unique}:%"),
        )
        .first()
    )
    assert fleet_cand is not None, f"expected fleet_wide candidate, summary={summary}"
    assert "3 shops" in fleet_cand.title
    # Priority should be bumped above the base
    assert (fleet_cand.priority_score or 0) >= 20


def test_two_shops_does_not_trigger_fleet_wide(db):
    """Below the 3-shop threshold the rule must NOT fire."""
    unique = f"two_shops_{uuid.uuid4().hex[:8]}"
    for shop_idx in range(2):
        _seed_alert(
            db,
            alert_type=unique,
            source=f"probe:other:shop_{shop_idx}",
            shop=f"shop{shop_idx}.myshopify.com",
        )

    run_bug_triage(db)

    fleet_cand = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like(f"fleet:{unique}:%"))
        .first()
    )
    assert fleet_cand is None


def test_three_shops_same_alert_type_different_template_no_fleet(db):
    """Different source templates must NOT collapse together."""
    unique = f"diff_template_{uuid.uuid4().hex[:8]}"
    _seed_alert(db, alert_type=unique, source=f"probe:rev:shop_a", shop="a.myshopify.com")
    _seed_alert(db, alert_type=unique, source=f"webhook:fail:shop_b", shop="b.myshopify.com")
    _seed_alert(db, alert_type=unique, source=f"deploy:retry:shop_c", shop="c.myshopify.com")

    run_bug_triage(db)

    # Each different template — none reaches 3 distinct shops with the same template
    fleet_cands = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like(f"fleet:{unique}:%"))
        .all()
    )
    assert len(fleet_cands) == 0


def test_frontend_error_excluded_from_fleet_wide(db):
    """frontend_error is visibility-only — fleet rule must skip it."""
    for shop_idx in range(5):
        _seed_alert(
            db,
            alert_type="frontend_error",
            source=f"fe:Component:abc{shop_idx}",
            shop=f"shop{shop_idx}.myshopify.com",
        )
    run_bug_triage(db)
    fleet_fe = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_type == "fleet_wide")
        .filter(BugFixCandidate.source_ref.like("fleet:frontend_error:%"))
        .first()
    )
    assert fleet_fe is None


def test_global_alerts_no_shop_excluded(db):
    """Alerts with shop_domain=NULL are global, not fleet candidates."""
    unique = f"global_{uuid.uuid4().hex[:8]}"
    for i in range(5):
        a = OpsAlert(
            created_at=_now(),
            severity="warning",
            source=f"probe:global:run_{i}",
            alert_type=unique,
            shop_domain=None,  # global, no shop
            summary="global probe",
            detail=json.dumps({"i": i}),
            resolved=False,
        )
        db.add(a)
    db.flush()

    run_bug_triage(db)

    fleet = (
        db.query(BugFixCandidate)
        .filter(BugFixCandidate.source_ref.like(f"fleet:{unique}:%"))
        .first()
    )
    assert fleet is None
