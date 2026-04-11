"""
Tests for the frontend error → self-healing pipeline bridge.

Verifies:
1. POST /ops/frontend-errors accepts valid payloads and writes an ops_alert
2. Fingerprinting collapses repeated identical errors into one source
3. run_bug_triage Rule 5 promotes a frontend_error alert into a BugFixCandidate
4. project_brain maps dashboard paths to the frontend domain tree
5. Invalid payloads are rejected cleanly (400), not crashed

Test isolation: scoped queries by source_ref to avoid collisions with rows
committed to the shared dev DB by production pipeline runs.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.bugfix_candidate import BugFixCandidate
from app.models.ops_alert import OpsAlert
from app.services.bugfix_pipeline import run_bug_triage
from app.services.project_brain import classify_file


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------------

def test_frontend_error_endpoint_accepts_valid_payload(client, db):
    payload = {
        "component": "NudgePerformance",
        "error_type": "TypeError",
        "message": "Cannot read property 'id' of undefined",
        "stack": "at NudgePerformance.tsx:42:15",
        "url": "https://app.hedgesparkhq.com/app",
        "severity": "warning",
    }
    resp = client.post("/ops/frontend-errors", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] is True
    assert body["source"].startswith("fe:NudgePerformance:")

    # Verify the alert was actually written (scoped by the returned source).
    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "frontend_error",
            OpsAlert.source == body["source"],
        )
        .first()
    )
    assert alert is not None
    assert alert.severity == "warning"
    assert "TypeError" in alert.summary


def test_frontend_error_fingerprint_collapses_duplicates(client, db):
    """Two identical reports from the same component produce the same source."""
    payload = {
        "component": "ProductConversions",
        "error_type": "FetchError",
        "message": "Failed to fetch /pro/heatmap",
    }
    r1 = client.post("/ops/frontend-errors", json=payload)
    r2 = client.post("/ops/frontend-errors", json=payload)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["source"] == r2.json()["source"]


def test_frontend_error_different_messages_different_fingerprints(client, db):
    """Different error messages get distinct source fingerprints."""
    r1 = client.post("/ops/frontend-errors", json={
        "component": "SameComponent",
        "error_type": "TypeError",
        "message": "first distinct error",
    })
    r2 = client.post("/ops/frontend-errors", json={
        "component": "SameComponent",
        "error_type": "TypeError",
        "message": "second distinct error",
    })
    assert r1.json()["source"] != r2.json()["source"]


def test_frontend_error_rejects_oversized_message(client):
    """Pydantic validation blocks payloads that exceed the message limit."""
    payload = {
        "component": "X",
        "error_type": "TypeError",
        "message": "A" * 10_000,
    }
    resp = client.post("/ops/frontend-errors", json=payload)
    assert resp.status_code == 422


def test_frontend_error_rejects_missing_required(client):
    resp = client.post("/ops/frontend-errors", json={"component": "X"})
    assert resp.status_code == 422


def test_frontend_error_redacts_obvious_secrets(client, db):
    """Bearer tokens in messages are redacted at the backend even if the
    client sends them verbatim."""
    payload = {
        "component": "AuthFlow",
        "error_type": "AuthError",
        "message": "Request failed: Bearer sk_live_abcdefghi123456 rejected",
    }
    resp = client.post("/ops/frontend-errors", json=payload)
    assert resp.status_code == 202
    alert = (
        db.query(OpsAlert)
        .filter(
            OpsAlert.alert_type == "frontend_error",
            OpsAlert.source == resp.json()["source"],
        )
        .first()
    )
    assert alert is not None
    assert "sk_live_abcdefghi123456" not in alert.summary
    assert "REDACTED" in alert.summary


# ---------------------------------------------------------------------------
# Triage Rule 5 — frontend_error → BugFixCandidate
# ---------------------------------------------------------------------------

def test_triage_promotes_frontend_error_to_candidate(db):
    """run_bug_triage Rule 5 creates a BugFixCandidate from a frontend_error alert."""
    alert = OpsAlert(
        severity="warning",
        source="fe:HoldoutToggle:abc12345",
        alert_type="frontend_error",
        summary="[HoldoutToggle] TypeError: save failed",
        shop_domain="test.myshopify.com",
        detail='{"stack":"at ..."}',
        created_at=_now(),
    )
    db.add(alert)
    db.flush()

    summary = run_bug_triage(db)
    assert summary["created"] >= 1

    # Scoped by the exact source_ref the triage builds from `alert.source`.
    c = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "frontend_error",
            BugFixCandidate.source_ref == "fe:HoldoutToggle:abc12345",
        )
        .first()
    )
    assert c is not None
    assert "HoldoutToggle" in c.title
    assert c.status == "open"


def test_triage_dedup_collapses_repeated_frontend_alerts(db):
    """Multiple alerts with the same source result in one candidate per cycle."""
    for i in range(3):
        db.add(OpsAlert(
            severity="warning",
            source="fe:ProductConversions:deadbeef",
            alert_type="frontend_error",
            summary=f"Fetch failed attempt {i}",
            created_at=_now(),
        ))
    db.flush()

    run_bug_triage(db)
    candidates = (
        db.query(BugFixCandidate)
        .filter(
            BugFixCandidate.source_type == "frontend_error",
            BugFixCandidate.source_ref == "fe:ProductConversions:deadbeef",
        )
        .all()
    )
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# project_brain classification for dashboard paths
# ---------------------------------------------------------------------------

def test_frontend_error_candidate_is_visibility_only_not_auto_proposed(db):
    """
    Phase-6 hardening: frontend_error candidates must NOT be auto-proposed
    by the LLM. The LLM lacks training on our specific React/Next codebase
    and auto-proposing .tsx patches produces apply_failed loops + wastes
    budget. The candidate still exists for operator visibility.
    """
    from app.services.bugfix_pipeline import run_auto_propose, is_visibility_only
    from app.services.bugfix_pipeline import _VISIBILITY_ONLY_SOURCE_TYPES

    assert "frontend_error" in _VISIBILITY_ONLY_SOURCE_TYPES
    assert is_visibility_only("frontend_error") is True
    assert is_visibility_only("ops_alert") is False

    # Inject a frontend_error candidate in the 'open' state
    c = BugFixCandidate(
        source_type="frontend_error",
        source_ref="fe:Test:deadbeef",
        title="Test frontend crash",
        summary="TypeError: test",
        status="open",
    )
    db.add(c)
    db.flush()

    summary = run_auto_propose(db)
    # proposal_attempted_at must remain None — the propose skipped it.
    db.refresh(c)
    assert c.proposal_attempted_at is None, (
        "frontend_error candidate must NOT be touched by auto_propose"
    )
    assert summary["skipped_visibility"] >= 1, (
        "skipped_visibility counter must report the held-back candidate"
    )


def test_project_brain_classifies_dashboard_component_as_frontend():
    result = classify_file("dashboard/src/app/components/NudgePerformance.tsx")
    assert result["domain"] == "frontend"
    assert result["criticality"] == "high"


def test_project_brain_classifies_billing_component_as_critical():
    result = classify_file("dashboard/src/app/components/billing/PlanCard.tsx")
    assert result["domain"] == "frontend_billing"
    assert result["criticality"] == "critical"


def test_project_brain_classifies_onboarding_path_as_critical():
    result = classify_file("dashboard/src/app/install/page.tsx")
    assert result["domain"] == "frontend_onboarding"
    assert result["criticality"] == "critical"
