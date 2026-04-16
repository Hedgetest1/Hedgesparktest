"""Tests for project brain + reviewer layer."""
import json
import os

import pytest
from sqlalchemy import text

from app.models.project_brain_snapshot import ProjectBrainSnapshot
from app.models.reviewer_assessment import ReviewerAssessment
from app.models.bugfix_candidate import BugFixCandidate
from app.models.evolution_proposal import EvolutionProposal
from app.services.project_brain import (
    build_codebase_index,
    build_runtime_state,
    build_full_snapshot,
    get_latest_snapshot,
    get_brain_summary,
    get_constitution,
    classify_file,
    CONSTITUTION_VERSION,
    SENSITIVE_DOMAINS,
    should_refresh_brain,
    mark_brain_refreshed,
)
from app.services.reviewer_layer import (
    review_entity,
    format_for_operator,
)


# ---------------------------------------------------------------------------
# Brain: Codebase Index
# ---------------------------------------------------------------------------

def test_codebase_index_structure():
    """build_codebase_index returns files, domains, and stats."""
    index = build_codebase_index()
    assert "files" in index
    assert "domains" in index
    assert "stats" in index
    assert index["stats"]["total_files"] > 0
    assert index["stats"]["services"] > 0
    assert index["stats"]["models"] > 0
    assert index["stats"]["apis"] > 0


def test_codebase_index_has_critical_files():
    """Codebase index identifies critical files."""
    index = build_codebase_index()
    critical = [f for f in index["files"] if f["criticality"] in ("critical", "high")]
    assert len(critical) > 0
    assert index["stats"]["critical_files"] > 0


def test_codebase_skips_venv():
    """Codebase index does not include venv files."""
    index = build_codebase_index()
    venv_files = [f for f in index["files"] if "venv/" in f["path"]]
    assert len(venv_files) == 0


# ---------------------------------------------------------------------------
# Brain: File Classification
# ---------------------------------------------------------------------------

def test_classify_billing_as_critical():
    """Billing paths are classified as critical."""
    c = classify_file("app/api/billing.py")
    assert c["domain"] == "billing"
    assert c["criticality"] == "critical"
    assert c["is_sensitive"] is True


def test_classify_auth_as_critical():
    """Auth/crypto paths are classified as critical."""
    c = classify_file("app/core/token_crypto.py")
    assert c["domain"] == "auth"
    assert c["criticality"] == "critical"
    assert c["is_sensitive"] is True


def test_classify_shopify_oauth_as_critical():
    """Shopify OAuth paths are classified as critical."""
    c = classify_file("app/api/shopify_oauth.py")
    assert c["domain"] == "shopify_auth"
    assert c["criticality"] == "critical"


def test_classify_webhooks_as_critical():
    """Webhook paths are classified as critical."""
    c = classify_file("app/api/webhooks.py")
    assert c["domain"] == "webhooks"
    assert c["criticality"] == "critical"


def test_classify_intelligence_as_low():
    """Intelligence engine paths are low criticality."""
    c = classify_file("app/services/intent_engine.py")
    assert c["domain"] == "intelligence"
    assert c["criticality"] == "low"
    assert c["is_sensitive"] is False


def test_classify_tests_as_low():
    """Test paths are low criticality."""
    c = classify_file("tests/test_something.py")
    assert c["domain"] == "tests"
    assert c["criticality"] == "low"


def test_classify_unknown_as_other():
    """Unknown paths get domain 'other' and low criticality."""
    c = classify_file("random/unknown/file.py")
    assert c["domain"] == "other"
    assert c["criticality"] == "low"


def test_classify_orchestrator_as_high():
    """Orchestrator paths are high criticality."""
    c = classify_file("app/services/orchestrator.py")
    assert c["domain"] == "orchestrator"
    assert c["criticality"] == "high"


# ---------------------------------------------------------------------------
# Brain: Constitution
# ---------------------------------------------------------------------------

def test_constitution_exists():
    """Constitution is defined and versioned."""
    c = get_constitution()
    assert c["version"] == CONSTITUTION_VERSION
    assert len(c["principles"]) >= 8


def test_constitution_has_protect_core():
    """Constitution includes protect_core principle."""
    c = get_constitution()
    ids = {p["id"] for p in c["principles"]}
    assert "protect_core" in ids
    assert "no_regressions" in ids
    assert "tier0_safety" in ids


# ---------------------------------------------------------------------------
# Brain: Snapshot Persistence
# ---------------------------------------------------------------------------

def test_brain_snapshot_persists(db):
    """build_full_snapshot creates a persisted row."""
    snapshot = build_full_snapshot(db)
    assert snapshot.id is not None
    assert snapshot.snapshot_type == "full"
    assert snapshot.total_files > 0
    assert snapshot.constitution_version == CONSTITUTION_VERSION
    assert snapshot.codebase_json is not None
    assert snapshot.runtime_json is not None


def test_get_latest_snapshot(db):
    """get_latest_snapshot returns the most recent snapshot."""
    s1 = build_full_snapshot(db)
    s2 = build_full_snapshot(db)
    latest = get_latest_snapshot(db)
    assert latest.id == s2.id


def test_brain_summary(db):
    """get_brain_summary returns structured operator-facing data."""
    build_full_snapshot(db)
    summary = get_brain_summary(db)
    assert summary["status"] == "active"
    assert "codebase" in summary
    assert "runtime" in summary
    assert "summary_stats" in summary
    assert summary["constitution_version"] == CONSTITUTION_VERSION


def test_brain_summary_without_snapshot(db):
    """get_brain_summary handles no-snapshot case."""
    # Clear any existing snapshots
    db.execute(text("DELETE FROM project_brain_snapshots"))
    db.flush()
    summary = get_brain_summary(db)
    assert summary["status"] == "no_snapshot"


# ---------------------------------------------------------------------------
# Brain: Runtime State
# ---------------------------------------------------------------------------

def test_runtime_state_structure(db):
    """build_runtime_state returns all expected sections."""
    state = build_runtime_state(db)
    assert "alerts" in state
    assert "bugfixes" in state
    assert "merges" in state
    assert "evolution" in state
    assert "model_config" in state
    assert "system_vitals" in state
    assert "support_incidents" in state
    assert "scaling" in state


# ---------------------------------------------------------------------------
# Brain: Cooldown
# ---------------------------------------------------------------------------

def test_brain_cooldown():
    """Brain cooldown respects 24h period."""
    import app.services.project_brain as brain_mod
    brain_mod._last_brain_run = None
    assert brain_mod.should_refresh_brain() is True

    brain_mod.mark_brain_refreshed()
    assert brain_mod.should_refresh_brain() is False

    brain_mod._last_brain_run = None  # reset


# ---------------------------------------------------------------------------
# Reviewer: Bugfix — Safe Domain
# ---------------------------------------------------------------------------

def test_review_bugfix_safe_domain(db):
    """Bugfix in a safe domain gets approve verdict."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="evolution", source_ref="evo_1",
        title="Fix test coverage", status="patch_proposed",
        patch_files=json.dumps(["app/services/intent_engine.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    assert assessment.verdict in ("approve", "approve_with_notes")
    assert assessment.risk_level == "low"
    assert assessment.auto_approvable is True
    assert "intelligence" in json.loads(assessment.affected_domains_json)


# ---------------------------------------------------------------------------
# Reviewer: Bugfix — Sensitive Domain (stricter)
# ---------------------------------------------------------------------------

def test_review_bugfix_sensitive_domain(db):
    """Bugfix touching billing must be stricter."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_1",
        title="Fix billing edge case", status="patch_proposed",
        patch_files=json.dumps(["app/api/billing.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        patch_risk_tier=1,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment is not None
    assert assessment.risk_level in ("high", "critical")
    assert assessment.auto_approvable is False
    assert "billing" in json.loads(assessment.affected_domains_json)


# ---------------------------------------------------------------------------
# Reviewer: TIER_2 always rejected
# ---------------------------------------------------------------------------

def test_review_tier2_rejected(db):
    """TIER_2 bugfix always gets reject verdict."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="ops_alert", source_ref="alert_t2",
        title="Dangerous patch", status="patch_proposed",
        patch_files=json.dumps(["app/services/something.py"]),
        patch_diff="big diff",
        patch_risk_tier=2,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    assert assessment.verdict == "reject"
    assert assessment.auto_approvable is False
    blocking = json.loads(assessment.blocking_concerns_json)
    assert any("TIER_2" in b for b in blocking)


# ---------------------------------------------------------------------------
# Reviewer: Evolution Proposal
# ---------------------------------------------------------------------------

def test_review_evolution_level3(db):
    """LEVEL_3 evolution proposal gets high risk and refine/approve_with_notes."""
    build_full_snapshot(db)
    p = EvolutionProposal(
        proposal_type="refactor",
        target_file="app/services/bugfix_pipeline.py",
        risk_level="LEVEL_3",
        reason="Split large service",
        expected_impact="Better maintainability",
        auto_applicable=False,
        status="open",
    )
    db.add(p)
    db.flush()

    assessment = review_entity(db, "evolution_proposal", p.id)
    assert assessment is not None
    assert assessment.risk_level in ("high", "critical")
    assert assessment.verdict in ("refine", "approve_with_notes")
    notes = json.loads(assessment.notes_json)
    assert any("LEVEL_3" in n for n in notes)


# ---------------------------------------------------------------------------
# Reviewer: Constitution influence
# ---------------------------------------------------------------------------

def test_review_tier0_on_sensitive_domain_flagged(db):
    """TIER_0 on sensitive domain triggers constitution concern."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="evolution", source_ref="evo_const",
        title="Auto-fix on auth", status="patch_proposed",
        patch_files=json.dumps(["app/core/deps.py"]),
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    notes = json.loads(assessment.notes_json) if assessment.notes_json else []
    assert any("protect_core" in n for n in notes)
    assert assessment.auto_approvable is False


# ---------------------------------------------------------------------------
# Reviewer: Entity not found
# ---------------------------------------------------------------------------

def test_review_entity_not_found(db):
    """review_entity returns None for missing entity."""
    result = review_entity(db, "bugfix_candidate", 999999)
    assert result is None


# ---------------------------------------------------------------------------
# Reviewer: Operator formatting
# ---------------------------------------------------------------------------

def test_format_for_operator(db):
    """format_for_operator produces compact readable text."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="evolution", source_ref="evo_fmt",
        title="Format test fix", status="patch_proposed",
        patch_files=json.dumps(["app/services/audit.py"]),
        patch_diff="small diff",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    msg = format_for_operator(assessment)
    assert "verdict:" in msg.lower()
    assert "risk:" in msg.lower()


# ---------------------------------------------------------------------------
# Reviewer: Assessment persistence
# ---------------------------------------------------------------------------

def test_assessment_persisted(db):
    """ReviewerAssessment is persisted to DB."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="evolution", source_ref="evo_persist",
        title="Persist test", status="open",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    assessment = review_entity(db, "bugfix_candidate", c.id)
    found = db.get(ReviewerAssessment, assessment.id)
    assert found is not None
    assert found.entity_type == "bugfix_candidate"
    assert found.entity_id == c.id
    assert found.reviewer_mode == "deterministic"


# ---------------------------------------------------------------------------
# API: Brain + Reviewer endpoints
# ---------------------------------------------------------------------------

def _op_headers():
    return {"X-API-Key": os.environ.get("DASHBOARD_API_KEY", "test-key")}


def test_api_brain_summary(db, client):
    """GET /ops/project-brain/summary returns brain state."""
    build_full_snapshot(db)
    resp = client.get("/ops/project-brain/summary", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert "codebase" in data


def test_api_brain_constitution(client):
    """GET /ops/project-brain/constitution returns constitution."""
    resp = client.get("/ops/project-brain/constitution", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == CONSTITUTION_VERSION
    assert len(data["principles"]) >= 8


def test_api_brain_refresh(db, client):
    """POST /ops/project-brain/refresh creates a snapshot."""
    resp = client.post(
        "/ops/project-brain/refresh",
        headers={**_op_headers(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "refreshed"
    assert data["total_files"] > 0


def test_api_reviewer_assess(db, client):
    """POST /ops/reviewer/assess reviews an entity."""
    build_full_snapshot(db)
    c = BugFixCandidate(
        source_type="evolution", source_ref="evo_api",
        title="API test", status="open",
        patch_risk_tier=0,
    )
    db.add(c)
    db.flush()

    resp = client.post(
        f"/ops/reviewer/assess?entity_type=bugfix_candidate&entity_id={c.id}",
        headers={**_op_headers(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "verdict" in data
    assert "risk_level" in data
    assert "operator_message" in data
    assert data["reviewer_mode"] == "deterministic"


def test_api_reviewer_auth_required(client):
    """Reviewer endpoints require operator auth."""
    resp = client.post("/ops/reviewer/assess?entity_type=bugfix_candidate&entity_id=1")
    assert resp.status_code in (401, 415, 503)


def test_api_reviewer_invalid_type(db, client):
    """Reviewer rejects invalid entity_type."""
    resp = client.post(
        "/ops/reviewer/assess?entity_type=invalid_thing&entity_id=1",
        headers={**_op_headers(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
