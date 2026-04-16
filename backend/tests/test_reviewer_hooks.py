"""Tests for reviewer hooks in bugfix pipeline, evolution converter, and Telegram output."""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.models.reviewer_assessment import ReviewerAssessment
from app.services.project_brain import build_full_snapshot


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _isolate(db):
    """Neutralize existing data that could interfere."""
    db.execute(text("UPDATE bugfix_candidates SET status = 'rejected' WHERE status IN ('open', 'patch_proposed')"))
    db.execute(text("UPDATE evolution_proposals SET status = 'expired' WHERE status = 'open'"))
    db.flush()


@pytest.fixture()
def brain(db):
    """Ensure a brain snapshot exists for the reviewer."""
    return build_full_snapshot(db)


# ---------------------------------------------------------------------------
# Bugfix auto-apply: reviewer blocks on reject
# ---------------------------------------------------------------------------

def test_reviewer_blocks_auto_apply_on_reject(db, brain):
    """When reviewer returns reject, auto-apply is blocked even for TIER_0."""
    from app.services.bugfix_pipeline import run_auto_apply, PATCH_TIER_0

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="test_block_1",
        title="Fix safe file", status="patch_proposed",
        patch_files=json.dumps(["tests/test_placeholder.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        patch_risk_tier=PATCH_TIER_0,
    )
    db.add(c)
    db.flush()

    # Mock reviewer to return reject
    mock_assessment = MagicMock()
    mock_assessment.id = 9999
    mock_assessment.verdict = "reject"
    mock_assessment.auto_approvable = False
    mock_assessment.blocking_concerns_json = json.dumps(["Test blocking concern"])
    mock_assessment.notes_json = None
    mock_assessment.summary = "Blocked by test"
    mock_assessment.risk_level = "critical"
    mock_assessment.strategic_alignment = "weak"
    mock_assessment.entity_type = "bugfix_candidate"
    mock_assessment.entity_id = c.id

    with patch("app.services.reviewer_layer.review_entity", return_value=mock_assessment):
        summary = run_auto_apply(db, max_per_cycle=1)

    db.refresh(c)
    assert c.status == "patch_proposed"  # not advanced
    assert summary["skipped"] >= 1
    assert c.reviewer_assessment_id == 9999


def test_reviewer_allows_auto_apply_safe_domain(db, brain):
    """Bugfix in safe domain with TIER_0 is allowed by reviewer."""
    from app.services.bugfix_pipeline import run_auto_apply, PATCH_TIER_0, apply_bugfix_candidate

    # source_type='manual' + affected_domain 'observability' keeps the
    # candidate out of the predictive-gate lookup (no historical 'manual'
    # rows exist in the dev DB for this domain, so the gate stays neutral).
    c = BugFixCandidate(
        source_type="manual", source_ref="test_allow_1",
        title="Fix test coverage", status="patch_proposed",
        patch_files=json.dumps(["tests/test_something.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        patch_risk_tier=PATCH_TIER_0,
        priority_score=100,  # win ORDER BY against pre-committed dev DB rows
        affected_domain="observability",
    )
    db.add(c)
    db.flush()

    # Mock apply_bugfix_candidate to avoid actual git operations
    mock_result = MagicMock()
    mock_result.status = "applied"
    mock_result.test_passed = True
    mock_result.health_ok = True
    mock_result.failure_reason = None

    with patch("app.services.bugfix_pipeline.apply_bugfix_candidate", return_value=mock_result):
        summary = run_auto_apply(db, max_per_cycle=1)

    db.refresh(c)
    assert c.status == "approved" or c.status == "applied"
    assert summary["attempted"] >= 1
    assert c.reviewer_assessment_id is not None

    # Check the assessment allowed it
    assessment = db.get(ReviewerAssessment, c.reviewer_assessment_id)
    assert assessment is not None
    assert assessment.auto_approvable is True
    assert assessment.verdict in ("approve", "approve_with_notes")


# ---------------------------------------------------------------------------
# Bugfix auto-apply: reviewer blocks TIER_2
# ---------------------------------------------------------------------------

def test_reviewer_blocks_tier2_bugfix(db, brain):
    """TIER_2 bugfix gets reject verdict from reviewer."""
    from app.services.reviewer_layer import review_entity

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="test_tier2",
        title="Dangerous change", status="patch_proposed",
        patch_files=json.dumps(["app/services/orchestrator.py"]),
        patch_diff="big diff",
        patch_risk_tier=2,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment.verdict == "reject"
    assert assessment.auto_approvable is False


# ---------------------------------------------------------------------------
# Evolution converter: reviewer gates conversion
# ---------------------------------------------------------------------------

def test_reviewer_allows_safe_evolution_conversion(db, brain):
    """Safe LEVEL_1 proposal passes reviewer and gets converted."""
    from app.services.evolution_converter import convert_eligible_proposals

    p = EvolutionProposal(
        proposal_type="reliability",
        target_file="app/services/audit.py",
        risk_level="LEVEL_1",
        reason="Missing test for audit.py",
        expected_impact="Better coverage",
        auto_applicable=True,
        status="open",
        audit_cycle="2026-W13",
        dedup_key="test:rh_safe_convert",
    )
    db.add(p)
    db.flush()

    summary = convert_eligible_proposals(db, max_per_cycle=1)

    db.refresh(p)
    # Should be converted — reviewer approves safe proposals
    assert p.status == "accepted"
    assert summary["converted"] >= 1
    assert p.reviewer_assessment_id is not None

    # Check assessment
    assessment = db.get(ReviewerAssessment, p.reviewer_assessment_id)
    assert assessment is not None
    assert assessment.verdict in ("approve", "approve_with_notes")


def test_reviewer_blocks_sensitive_evolution_conversion(db, brain):
    """LEVEL_1 proposal targeting sensitive file is blocked by reviewer."""
    from app.services.evolution_converter import convert_eligible_proposals

    p = EvolutionProposal(
        proposal_type="reliability",
        target_file="app/core/token_crypto.py",
        risk_level="LEVEL_1",
        reason="Add test for token crypto",
        expected_impact="Better coverage",
        auto_applicable=True,
        status="open",
        audit_cycle="2026-W13",
        dedup_key="test:rh_block_convert",
    )
    db.add(p)
    db.flush()

    summary = convert_eligible_proposals(db, max_per_cycle=1)

    db.refresh(p)
    # Blocked by either tier_check (TIER_2 file) or reviewer (sensitive domain)
    # Proposal stays open (not converted)
    assert p.status == "open"
    assert summary["skipped_ineligible"] >= 1
    # reviewer_assessment_id may be None if tier_check blocked before reviewer ran
    # (tier_check catches TIER_2 earlier — defense in depth)


# ---------------------------------------------------------------------------
# Assessment IDs linked
# ---------------------------------------------------------------------------

def test_assessment_id_linked_on_bugfix(db, brain):
    """reviewer_assessment_id is set on BugFixCandidate after review."""
    from app.services.reviewer_layer import review_entity

    c = BugFixCandidate(
        source_type="evolution", source_ref="test_link",
        title="Link test", status="open",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    c.reviewer_assessment_id = assessment.id
    db.flush()

    db.refresh(c)
    assert c.reviewer_assessment_id == assessment.id
    assert assessment.entity_type == "bugfix_candidate"
    assert assessment.entity_id == c.id


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def test_telegram_reviewer_verdict_format(db, brain):
    """send_reviewer_verdict produces a well-formatted message."""
    from app.services.reviewer_layer import review_entity
    from app.services.telegram_agent import send_reviewer_verdict

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="test_tg_fmt",
        title="Fix webhook handler", status="patch_proposed",
        patch_files=json.dumps(["app/api/webhooks.py"]),
        patch_diff="small fix",
        patch_risk_tier=1,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)

    # Mock send_message to capture the text
    with patch("app.services.telegram_agent.send_message") as mock_send:
        mock_send.return_value = True
        result = send_reviewer_verdict(assessment, entity_title=c.title)

    assert mock_send.called
    sent_text = mock_send.call_args[0][0]
    # Decision-first format: should contain a decision headline and the entity title
    assert "Fix webhook handler" in sent_text
    # Should contain a decision (green/yellow/red)
    assert any(phrase in sent_text for phrase in [
        "You can proceed", "Proceed with caution",
        "Needs improvement", "Do NOT proceed",
    ])


def test_telegram_verdict_includes_blocking_concerns(db, brain):
    """Telegram verdict message includes blocking concerns for TIER_2."""
    from app.services.reviewer_layer import review_entity
    from app.services.telegram_agent import send_reviewer_verdict

    c = BugFixCandidate(
        source_type="ops_alert", source_ref="test_tg_block",
        title="Dangerous patch", status="patch_proposed",
        patch_files=json.dumps(["app/services/something.py"]),
        patch_diff="big diff",
        patch_risk_tier=2,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)

    with patch("app.services.telegram_agent.send_message") as mock_send:
        mock_send.return_value = True
        send_reviewer_verdict(assessment, entity_title=c.title)

    sent_text = mock_send.call_args[0][0]
    # Decision-first: TIER_2 patches should show red/blocked decision
    assert "Do NOT proceed" in sent_text
    # Blocking concerns should appear as explanation bullets
    assert "TIER_2" in sent_text
    assert "Do not apply" in sent_text


# ---------------------------------------------------------------------------
# Audit log includes reviewer assessment
# ---------------------------------------------------------------------------

def test_audit_log_includes_reviewer_id(db, brain):
    """Auto-apply audit log metadata includes reviewer_assessment_id."""
    from app.services.bugfix_pipeline import run_auto_apply, PATCH_TIER_0

    c = BugFixCandidate(
        source_type="evolution", source_ref="test_audit_rev",
        title="Safe fix", status="patch_proposed",
        patch_files=json.dumps(["tests/test_placeholder.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y",
        patch_risk_tier=PATCH_TIER_0,
    )
    db.add(c)
    db.flush()

    mock_result = MagicMock()
    mock_result.status = "applied"
    mock_result.test_passed = True
    mock_result.health_ok = True
    mock_result.failure_reason = None

    with patch("app.services.bugfix_pipeline.apply_bugfix_candidate", return_value=mock_result):
        run_auto_apply(db, max_per_cycle=1)

    # Check audit log
    audit = db.execute(text(
        "SELECT metadata_json FROM audit_log "
        "WHERE action_type = 'bugfix_auto_approved' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    metadata = json.loads(audit[0])
    assert "reviewer_assessment_id" in metadata
    assert metadata["reviewer_assessment_id"] is not None


# ---------------------------------------------------------------------------
# Operator API includes reviewer_assessment_id
# ---------------------------------------------------------------------------

def test_api_bugfix_includes_reviewer_id(db, client, brain):
    """GET /ops/bugfixes/{id} response includes reviewer_assessment_id."""
    import os
    from app.services.reviewer_layer import review_entity

    key = os.environ.get("DASHBOARD_API_KEY", "test-key")

    c = BugFixCandidate(
        source_type="evolution", source_ref="test_api_rev",
        title="API reviewer test", status="open",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    c.reviewer_assessment_id = assessment.id
    db.flush()

    resp = client.get(f"/ops/bugfixes/{c.id}", headers={"X-API-Key": key})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reviewer_assessment_id"] == assessment.id
