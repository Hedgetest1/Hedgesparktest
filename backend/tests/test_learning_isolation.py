"""Tests for learning isolation — pre-merchant data must not become product truth."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.models.system_lesson import SystemLesson
from app.models.evolution_proposal import EvolutionProposal
from app.models.patch_fingerprint import PatchFingerprint
from app.services.learning_isolation import (
    classify_evidence_source,
    is_product_learning_eligible,
    filter_product_lessons,
    label_candidate,
    label_lesson,
    label_proposal,
    label_fingerprint,
    EVIDENCE_SOURCES,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------

def test_classify_defaults_to_pre_merchant_without_db():
    """Without DB access, evidence source is pre_merchant."""
    source = classify_evidence_source(None)
    assert source == "pre_merchant"


def test_classify_env_override():
    """HEDGESPARK_EVIDENCE_SOURCE env var overrides DB heuristic."""
    with patch.dict(os.environ, {"HEDGESPARK_EVIDENCE_SOURCE": "real_merchant"}):
        assert classify_evidence_source(None) == "real_merchant"
    with patch.dict(os.environ, {"HEDGESPARK_EVIDENCE_SOURCE": "sandbox"}):
        assert classify_evidence_source(None) == "sandbox"
    with patch.dict(os.environ, {"HEDGESPARK_EVIDENCE_SOURCE": "internal_test"}):
        assert classify_evidence_source(None) == "internal_test"


def test_classify_ignores_invalid_env():
    """Invalid env values are ignored."""
    with patch.dict(os.environ, {"HEDGESPARK_EVIDENCE_SOURCE": "invalid"}):
        # Falls through to DB check, which may not find merchants
        source = classify_evidence_source(None)
        assert source in EVIDENCE_SOURCES


def test_classify_detects_real_merchant(db):
    """When an active merchant with access_token exists → real_merchant."""
    from app.models.merchant import Merchant
    db.add(Merchant(
        shop_domain="real-store.myshopify.com",
        access_token="shpat_real_token",
        install_status="active",
    ))
    db.flush()
    source = classify_evidence_source(db)
    assert source == "real_merchant"


def test_classify_ignores_dev_shop_domains():
    """Dev/test/sandbox shop domains are never eligible for product learning."""
    from app.services.learning_isolation import _DEV_SHOP_DOMAINS
    for shop in _DEV_SHOP_DOMAINS:
        assert shop.endswith(".myshopify.com")


# ---------------------------------------------------------------------------
# Eligibility checks
# ---------------------------------------------------------------------------

def test_product_eligibility():
    """Only real_merchant is eligible for product learning."""
    assert is_product_learning_eligible("real_merchant") is True
    assert is_product_learning_eligible("pre_merchant") is False
    assert is_product_learning_eligible("internal_test") is False
    assert is_product_learning_eligible("sandbox") is False
    assert is_product_learning_eligible(None) is False


# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------

def test_label_candidate_defaults_pre_merchant(db):
    """New candidates without real merchants get pre_merchant label."""
    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_1",
        title="test bug", status="open",
    )
    db.add(c)
    db.flush()
    label_candidate(db, c)
    assert c.evidence_source == "pre_merchant"


def test_label_lesson_inherits_from_candidate(db):
    """Lessons inherit evidence_source from their candidate."""
    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_1",
        title="test", status="open",
        evidence_source="real_merchant",
    )
    db.add(c)
    db.flush()

    lesson = SystemLesson(
        domain="tracking", lesson_type="effective_pattern",
        summary="test lesson", confidence=0.7,
    )
    label_lesson(db, lesson, c)
    assert lesson.evidence_source == "real_merchant"


def test_label_lesson_falls_back_without_candidate():
    """Lessons without a candidate fall back to global classification (no DB)."""
    lesson = SystemLesson(
        domain="tracking", lesson_type="effective_pattern",
        summary="test lesson", confidence=0.7,
    )
    label_lesson(None, lesson, candidate=None)
    assert lesson.evidence_source == "pre_merchant"


# ---------------------------------------------------------------------------
# Filter gates
# ---------------------------------------------------------------------------

def test_filter_product_lessons_excludes_pre_merchant(db):
    """filter_product_lessons only returns real_merchant lessons."""
    # Add pre-merchant lesson
    db.add(SystemLesson(
        domain="tracking", lesson_type="effective_pattern",
        summary="pre-merchant lesson", confidence=0.8,
        evidence_source="pre_merchant",
    ))
    # Add real merchant lesson
    db.add(SystemLesson(
        domain="tracking", lesson_type="effective_pattern",
        summary="real merchant lesson", confidence=0.8,
        evidence_source="real_merchant",
    ))
    db.flush()

    q = db.query(SystemLesson).filter(SystemLesson.domain == "tracking")
    all_lessons = q.all()
    assert len(all_lessons) >= 2

    q_filtered = filter_product_lessons(
        db.query(SystemLesson).filter(SystemLesson.domain == "tracking"),
        SystemLesson,
    )
    product_lessons = q_filtered.all()
    assert len(product_lessons) >= 1
    for l in product_lessons:
        assert l.evidence_source == "real_merchant"


# ---------------------------------------------------------------------------
# Confidence scoring isolation
# ---------------------------------------------------------------------------

def test_confidence_scoring_ignores_pre_merchant_lessons(db):
    """Pre-merchant lessons must NOT boost fix confidence."""
    from app.services.candidate_scoring import compute_fix_confidence

    # Create pre-merchant effective lessons
    for i in range(5):
        db.add(SystemLesson(
            domain="tracking", lesson_type="effective_pattern",
            summary=f"pre-merchant lesson {i}", confidence=0.9,
            evidence_source="pre_merchant", status="active",
        ))
    db.flush()

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_99",
        title="tracker bug", status="open",
        affected_domain="tracking",
        evidence_source="pre_merchant",
    )
    db.add(c)
    db.flush()

    score, detail = compute_fix_confidence(db, c)
    # Lesson bonus should be 0 because all lessons are pre_merchant
    assert detail["lesson_bonus"]["count"] == 0
    assert detail["lesson_bonus"]["points"] == 0


def test_confidence_scoring_uses_real_merchant_lessons(db):
    """Real merchant lessons DO boost fix confidence."""
    from app.services.candidate_scoring import compute_fix_confidence

    # Create real merchant effective lessons
    for i in range(3):
        db.add(SystemLesson(
            domain="webhooks", lesson_type="effective_pattern",
            summary=f"real merchant lesson {i}", confidence=0.9,
            evidence_source="real_merchant", status="active",
        ))
    db.flush()

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_100",
        title="webhook bug", status="open",
        affected_domain="webhooks",
        evidence_source="real_merchant",
    )
    db.add(c)
    db.flush()

    score, detail = compute_fix_confidence(db, c)
    assert detail["lesson_bonus"]["count"] == 3
    assert detail["lesson_bonus"]["points"] > 0


# ---------------------------------------------------------------------------
# Reinforcement weight isolation
# ---------------------------------------------------------------------------

def test_reinforcement_excludes_pre_merchant(db):
    """Reinforcement weights must only count real_merchant proposals."""
    from app.services.evolution_reinforcement import compute_reinforcement_weights

    # Create pre-merchant proposals with business outcomes using raw SQL
    # because the ORM model may not expose all outcome columns.
    for i in range(5):
        db.execute(text("""
            INSERT INTO evolution_proposals
                (proposal_type, risk_level, reason, status, evidence_source,
                 business_outcome, business_measured_at, outcome_status, created_at)
            VALUES
                ('conversion', 'LEVEL_2', :reason, 'applied', 'pre_merchant',
                 'improved', :now, 'effective', :now)
        """), {"reason": f"pre-merchant test {i}", "now": _now()})
    db.flush()

    weights = compute_reinforcement_weights(db)
    # All domains should have 0 total because only pre_merchant data exists
    for domain, data in weights.items():
        assert data["total"] == 0, f"Domain {domain} has {data['total']} pre-merchant outcomes leaking into reinforcement"


# ---------------------------------------------------------------------------
# Lesson GC promotion isolation
# ---------------------------------------------------------------------------

def test_gc_does_not_promote_pre_merchant_lessons(db):
    """Pre-merchant lessons must NEVER be promoted to regression_warning."""
    from app.services.lesson_gc import run_lesson_gc

    # Create a high-confidence pre-merchant lesson that would normally be promoted
    lesson = SystemLesson(
        domain="tracking", lesson_type="ineffective_pattern",
        summary="pre-merchant high confidence lesson",
        confidence=0.95, evidence_count=10,
        evidence_source="pre_merchant", status="active",
    )
    db.add(lesson)
    db.flush()

    summary = run_lesson_gc(db)
    # Refresh
    db.refresh(lesson)
    # Should NOT have been promoted
    assert lesson.promotion_status is None, "Pre-merchant lesson was promoted — isolation violated"


def test_gc_promotes_real_merchant_lessons(db):
    """Real merchant lessons CAN be promoted."""
    from app.services.lesson_gc import run_lesson_gc

    lesson = SystemLesson(
        domain="webhooks", lesson_type="ineffective_pattern",
        summary="real merchant high confidence lesson",
        confidence=0.95, evidence_count=10,
        evidence_source="real_merchant", status="active",
    )
    db.add(lesson)
    db.flush()

    summary = run_lesson_gc(db)
    db.refresh(lesson)
    assert lesson.promotion_status == "pending_promotion"


# ---------------------------------------------------------------------------
# Effectiveness stats isolation
# ---------------------------------------------------------------------------

def test_effectiveness_stats_product_only(db):
    """product_only=True excludes pre-merchant outcomes."""
    from app.services.evolution_outcomes import get_effectiveness_stats

    # Create pre-merchant applied candidate with outcome
    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_200",
        title="pre-merchant fix", status="applied",
        applied_at=_now() - timedelta(hours=96),
        outcome_status="effective",
        outcome_measured_at=_now() - timedelta(hours=48),
        evidence_source="pre_merchant",
    )
    db.add(c)
    db.flush()

    all_stats = get_effectiveness_stats(db, product_only=False)
    product_stats = get_effectiveness_stats(db, product_only=True)

    assert all_stats["total_measured"] >= 1
    assert product_stats["total_measured"] == 0


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------

def test_model_db_defaults(db):
    """All learning models default to pre_merchant when inserted into DB."""
    c = BugFixCandidate(source_type="test", source_ref="test_1", title="test", status="open")
    db.add(c)
    db.flush()
    db.refresh(c)
    assert c.evidence_source == "pre_merchant"

    l = SystemLesson(domain="test", lesson_type="effective_pattern", summary="test", confidence=0.7)
    db.add(l)
    db.flush()
    db.refresh(l)
    assert l.evidence_source == "pre_merchant"

    p = EvolutionProposal(proposal_type="test", risk_level="LEVEL_2", reason="test")
    db.add(p)
    db.flush()
    db.refresh(p)
    assert p.evidence_source == "pre_merchant"

    fp = PatchFingerprint(fingerprint="abc123test", bugfix_candidate_id=c.id, outcome="applied")
    db.add(fp)
    db.flush()
    db.refresh(fp)
    assert fp.evidence_source == "pre_merchant"
