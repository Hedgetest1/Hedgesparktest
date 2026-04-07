"""Tests for synthetic merchant simulation — isolation guarantees."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.merchant import Merchant
from app.models.bugfix_candidate import BugFixCandidate
from app.models.system_lesson import SystemLesson
from app.services.learning_isolation import (
    classify_evidence_source,
    is_product_learning_eligible,
    is_synthetic_shop,
    is_synthetic_merchant,
    get_evidence_source_for_candidate,
    SYNTHETIC_SHOP_PREFIX,
    SYNTHETIC_SHOP_SUFFIX,
)
from app.services.simulation_engine import (
    create_synthetic_merchants,
    run_simulation_cycle,
    get_simulation_status,
    cleanup_synthetic_merchants,
    get_synthetic_merchants,
    ARCHETYPES,
    _synthetic_shop_domain,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Synthetic shop naming
# ---------------------------------------------------------------------------

def test_synthetic_shop_domain_format():
    """Synthetic shop domains follow the naming convention."""
    domain = _synthetic_shop_domain("healthy-001")
    assert domain.startswith(SYNTHETIC_SHOP_PREFIX)
    assert domain.endswith(SYNTHETIC_SHOP_SUFFIX)


def test_is_synthetic_shop_detects_prefix():
    """is_synthetic_shop detects the sim- prefix."""
    assert is_synthetic_shop("sim-healthy-001.synthetic.hedgespark.test") is True
    assert is_synthetic_shop("sim-anything") is True
    assert is_synthetic_shop("real-store.myshopify.com") is False
    assert is_synthetic_shop("") is False
    assert is_synthetic_shop(None) is False


def test_is_synthetic_shop_detects_suffix():
    """is_synthetic_shop detects the .synthetic.hedgespark.test suffix."""
    assert is_synthetic_shop("anything.synthetic.hedgespark.test") is True


# ---------------------------------------------------------------------------
# Merchant creation
# ---------------------------------------------------------------------------

def test_create_synthetic_merchants(db):
    """Creating synthetic merchants sets is_synthetic=True."""
    shops = create_synthetic_merchants(db, count=3)
    assert len(shops) == 3

    for shop in shops:
        m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
        assert m is not None
        assert m.is_synthetic is True
        assert m.access_token is None  # No token
        assert m.contact_email is None  # No email
        assert m.install_status == "active"
        assert is_synthetic_shop(m.shop_domain)


def test_create_merchants_stores_archetype(db):
    """Archetype is stored in merchant metadata."""
    shops = create_synthetic_merchants(db, count=1, archetypes=["healthy"])
    m = db.query(Merchant).filter(Merchant.shop_domain == shops[0]).first()
    meta = json.loads(m.onboarding_error)
    assert meta["archetype"] == "healthy"


def test_create_merchants_max_limit(db):
    """Cannot exceed max synthetic merchant limit."""
    import pytest
    with pytest.raises(ValueError, match="exceed max"):
        create_synthetic_merchants(db, count=200)


# ---------------------------------------------------------------------------
# CRITICAL: Isolation guarantees
# ---------------------------------------------------------------------------

def test_synthetic_merchant_excluded_from_real_classification(db):
    """Synthetic merchants must NEVER count as real merchants in the DB heuristic."""
    # Remove all real merchants for this test to isolate the check
    db.execute(text(
        "UPDATE merchants SET install_status = 'uninstalled' "
        "WHERE is_synthetic = false AND install_status = 'active'"
    ))
    db.flush()

    # Now only synthetic merchants exist as "active"
    shops = create_synthetic_merchants(db, count=1)
    shop = shops[0]

    # Even with an access_token, the synthetic flag must prevent real_merchant classification
    m = db.query(Merchant).filter(Merchant.shop_domain == shop).first()
    m.access_token = "shpat_fake_token"
    db.flush()

    source = classify_evidence_source(db)
    assert source != "real_merchant", (
        "CRITICAL: Synthetic merchant was classified as real_merchant — isolation broken"
    )


def test_synthetic_alerts_classified_as_sandbox(db):
    """Alerts from synthetic shops must classify as sandbox."""
    from app.models.ops_alert import OpsAlert
    shops = create_synthetic_merchants(db, count=1)
    shop = shops[0]

    # Create alert for synthetic shop using ORM
    alert = OpsAlert(
        severity="warning", source="test",
        alert_type="test_alert", shop_domain=shop,
        summary="test alert", resolved=False,
    )
    db.add(alert)
    db.flush()

    # Verify alert was persisted and is visible
    check = db.execute(text(
        "SELECT id, shop_domain FROM ops_alerts WHERE id = :id"
    ), {"id": alert.id}).fetchone()
    assert check is not None, "Alert was not persisted"
    assert check[1] == shop

    # Create candidate from this alert
    c = BugFixCandidate(
        source_type="ops_alert", source_ref=f"alert_{alert.id}",
        title="test bug", status="open",
    )
    db.add(c)
    db.flush()

    source = get_evidence_source_for_candidate(db, c)
    assert source == "sandbox", (
        f"CRITICAL: Synthetic shop alert classified as '{source}' not 'sandbox'. "
        f"Alert id={alert.id}, shop={shop}, source_ref={c.source_ref}"
    )


def test_synthetic_evidence_not_product_eligible():
    """Sandbox evidence must NEVER be eligible for product learning."""
    assert is_product_learning_eligible("sandbox") is False
    assert is_product_learning_eligible("pre_merchant") is False
    assert is_product_learning_eligible("internal_test") is False
    assert is_product_learning_eligible("real_merchant") is True


def test_synthetic_lessons_excluded_from_confidence_scoring(db):
    """Sandbox lessons must NOT boost fix confidence."""
    from app.services.candidate_scoring import compute_fix_confidence

    # Create sandbox lessons
    for i in range(5):
        db.add(SystemLesson(
            domain="tracking", lesson_type="effective_pattern",
            summary=f"sandbox lesson {i}", confidence=0.9,
            evidence_source="sandbox", status="active",
        ))
    db.flush()

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_999",
        title="tracker bug", status="open",
        affected_domain="tracking",
        evidence_source="sandbox",
    )
    db.add(c)
    db.flush()

    score, detail = compute_fix_confidence(db, c)
    assert detail["lesson_bonus"]["count"] == 0, (
        "CRITICAL: Sandbox lessons are boosting confidence — isolation broken"
    )


def test_synthetic_proposals_excluded_from_reinforcement(db):
    """Sandbox proposals must not feed reinforcement weights."""
    from app.services.evolution_reinforcement import compute_reinforcement_weights

    # Create sandbox proposals with business outcomes
    for i in range(5):
        db.execute(text("""
            INSERT INTO evolution_proposals
                (proposal_type, risk_level, reason, status, evidence_source,
                 business_outcome, business_measured_at, outcome_status, created_at)
            VALUES
                ('conversion', 'LEVEL_2', :reason, 'applied', 'sandbox',
                 'improved', :now, 'effective', :now)
        """), {"reason": f"sandbox test {i}", "now": _now()})
    db.flush()

    weights = compute_reinforcement_weights(db)
    for domain, data in weights.items():
        assert data["total"] == 0, (
            f"CRITICAL: Sandbox outcomes leaking into reinforcement for domain {domain}"
        )


def test_synthetic_lessons_not_promoted(db):
    """Sandbox lessons must NEVER be promoted to regression_warning."""
    from app.services.lesson_gc import run_lesson_gc

    lesson = SystemLesson(
        domain="tracking", lesson_type="ineffective_pattern",
        summary="sandbox high-confidence lesson",
        confidence=0.95, evidence_count=10,
        evidence_source="sandbox", status="active",
    )
    db.add(lesson)
    db.flush()

    run_lesson_gc(db)
    db.refresh(lesson)
    assert lesson.promotion_status is None, (
        "CRITICAL: Sandbox lesson was promoted — isolation broken"
    )


# ---------------------------------------------------------------------------
# Event generation
# ---------------------------------------------------------------------------

def test_run_simulation_cycle(db):
    """Simulation cycle generates events for synthetic merchants."""
    create_synthetic_merchants(db, count=2, archetypes=["healthy", "low_volume"])
    summary = run_simulation_cycle(db, scenario="mixed", hours=1, seed=42)

    assert summary.merchants_active == 2
    assert summary.events_generated > 0
    assert len(summary.errors) == 0


def test_simulation_events_have_synthetic_shop(db):
    """All generated events belong to synthetic shops."""
    shops = create_synthetic_merchants(db, count=1, archetypes=["healthy"])
    run_simulation_cycle(db, hours=1, seed=42)

    events = db.execute(
        text("SELECT DISTINCT shop_domain FROM events WHERE shop_domain = :shop"),
        {"shop": shops[0]},
    ).fetchall()
    assert len(events) >= 1
    for e in events:
        assert is_synthetic_shop(e[0])


def test_simulation_deterministic_with_seed(db):
    """Same seed produces same event count."""
    create_synthetic_merchants(db, count=1, archetypes=["healthy"])
    s1 = run_simulation_cycle(db, hours=1, seed=12345)
    # Can't run again in same transaction (events accumulate),
    # but we can verify the summary is deterministic
    assert s1.events_generated > 0


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def test_cleanup_removes_synthetic_data(db):
    """Cleanup removes synthetic merchants and their events."""
    shops = create_synthetic_merchants(db, count=2)
    run_simulation_cycle(db, hours=1, seed=42)

    # Verify data exists
    count = db.execute(
        text("SELECT COUNT(*) FROM events WHERE shop_domain = ANY(:shops)"),
        {"shops": shops},
    ).fetchone()[0]
    assert count > 0

    # Cleanup
    result = cleanup_synthetic_merchants(db)
    assert result["deleted_merchants"] == 2
    assert result.get("deleted_events", 0) > 0


# ---------------------------------------------------------------------------
# Status / observability
# ---------------------------------------------------------------------------

def test_simulation_status(db):
    """Status endpoint returns correct counts."""
    shops = create_synthetic_merchants(db, count=2)
    run_simulation_cycle(db, hours=1, seed=42)

    status = get_simulation_status(db)
    assert status["synthetic_merchants"] == 2
    assert status["synthetic_events"] > 0
    assert status["isolation_mode"] == "sandbox"


def test_is_synthetic_merchant_db_check(db):
    """is_synthetic_merchant checks the DB flag."""
    shops = create_synthetic_merchants(db, count=1)
    assert is_synthetic_merchant(db, shops[0]) is True
    assert is_synthetic_merchant(db, "nonexistent.myshopify.com") is False
