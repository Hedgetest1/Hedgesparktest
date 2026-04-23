"""Tests for bug triage, patch proposal, and approval pipeline."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.ops_alert import OpsAlert
from app.models.action_outcome import ActionOutcome
from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import run_bug_triage, propose_patch

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

def test_triage_creates_candidate_from_gdpr_alert(db):
    """GDPR failure alert → creates BugFixCandidate."""
    alert = OpsAlert(
        severity="critical", source="gdpr_processor",
        alert_type="gdpr_failure", summary="GDPR failed",
        shop_domain="test.myshopify.com", created_at=_now(),
    )
    db.add(alert)
    db.flush()

    summary = run_bug_triage(db)
    assert summary["created"] >= 1

    # Scope by the deterministic source_ref the triage builds ("alert_{id}"),
    # otherwise the query picks up historical 'ops_alert' candidates already
    # committed to the shared dev DB by real pipeline runs.
    c = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "ops_alert",
        BugFixCandidate.source_ref == f"alert_{alert.id}",
    ).first()
    assert c is not None
    assert "GDPR" in c.title
    assert c.status == "open"


def test_triage_creates_candidate_from_worker_failure(db):
    """Worker repeated failure alert → creates BugFixCandidate."""
    db.add(OpsAlert(
        severity="warning", source="intelligence_worker",
        alert_type="worker_repeated_failure", summary="3 errors",
        created_at=_now(),
    ))
    db.flush()

    summary = run_bug_triage(db)
    assert summary["created"] >= 1


def test_triage_creates_candidate_from_repeated_outcomes(db):
    """3+ no_effect outcomes → creates BugFixCandidate."""
    for i in range(4):
        db.add(ActionOutcome(
            audit_log_id=500 + i, action_type="orch_webhook_repair",
            target_id="broken.myshopify.com",
            executed_at=_now() - timedelta(hours=i),
            evaluated_at=_now(), outcome_status="no_effect",
        ))
    db.flush()

    summary = run_bug_triage(db)
    # Scope by the deterministic source_ref triage builds for this action_type
    # + target pair, otherwise we can pick up historical 'outcome' candidates
    # already committed to the shared dev DB by real pipeline runs.
    outcome_candidates = db.query(BugFixCandidate).filter(
        BugFixCandidate.source_type == "outcome",
        BugFixCandidate.source_ref == "outcome_orch_webhook_repair_broken.myshopify.com",
    ).all()
    assert len(outcome_candidates) >= 1


def test_triage_dedup_prevents_duplicate(db):
    """Same source_ref → no duplicate open candidate."""
    db.add(OpsAlert(
        severity="critical", source="gdpr_processor",
        alert_type="gdpr_failure", summary="fail1",
        created_at=_now(),
    ))
    db.flush()

    s1 = run_bug_triage(db)
    db.flush()
    s2 = run_bug_triage(db)

    assert s1["created"] >= 1
    assert s2["deduped"] >= 1


# ---------------------------------------------------------------------------
# Patch proposal (mocked LLM)
# ---------------------------------------------------------------------------

def test_propose_patch_stores_result(db):
    """Patch proposal stores diff on candidate row."""
    c = BugFixCandidate(
        source_type="manual", source_ref="test",
        title="Test bug", summary="Something broke",
        status="open",
    )
    db.add(c)
    db.flush()

    mock_response = json.dumps({
        "patch_summary": "Add test for alerting module",
        "files": ["tests/test_mock_stores.py"],
        "diff": "--- /dev/null\n+++ b/tests/test_mock_stores.py\n@@ -0,0 +1 @@\n+# test placeholder\n",
        "test_command": "python -m pytest tests/test_mock_stores.py -v",
    })

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "anthropic", "claude-sonnet-4-6"),
    ):
        result = propose_patch(db, c.id)

    assert result is True
    db.refresh(c)
    assert c.status == "patch_proposed"
    assert c.patch_summary == "Add test for alerting module"
    assert "test_mock_stores" in c.patch_diff
    assert c.test_command is not None
    # proposal_provider is set from the actual return tuple (2026-04-23 fix)
    assert c.proposal_provider == "anthropic"


def test_propose_patch_records_provider_even_on_downstream_validation_failure(db):
    """
    E2E probe on 2026-04-23 surfaced a latent observability gap:
    `candidate.proposal_provider` was only persisted when the WHOLE propose
    flow succeeded. When a downstream validator (diff-structure,
    diff-semantics, security-guard, post-LLM fingerprint) rejected the
    patch, the row said `proposal_provider=None` even though the LLM had
    been called and Anthropic/OpenAI had been billed. This made cost
    attribution and post-hoc "which provider failed most" queries
    impossible.

    Contract: once _call_llm returns a non-empty provider sentinel, the
    caller MUST persist it on the candidate immediately, BEFORE any
    validation gate.
    """
    c = BugFixCandidate(
        source_type="manual", source_ref="provider-preserve-test",
        title="Validation-rejected patch preserves provider",
        summary="Diff missing leading space on context line",
        status="open",
    )
    db.add(c)
    db.flush()

    # LLM returns a syntactically well-formed JSON wrapper but the diff
    # itself is malformed (context line with no leading space) — this is
    # the exact class of failure seen empirically in the prod probe.
    bad_diff_response = json.dumps({
        "patch_summary": "Add docstring",
        "files": ["tests/test_provider_preserve.py"],
        "diff": (
            "--- a/tests/test_provider_preserve.py\n"
            "+++ b/tests/test_provider_preserve.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+\"\"\"\n"
            "this context line has no leading space — MALFORMED\n"
            "\"\"\"\n"
            " import os\n"
        ),
        "test_command": "",
    })

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(bad_diff_response, "anthropic", "claude-sonnet-4-6"),
    ):
        result = propose_patch(db, c.id)

    db.refresh(c)
    # Propose should have FAILED at a downstream validator
    assert result is False
    assert c.status == "analyzed"
    assert c.failure_reason is not None
    # …BUT provenance must still be on the row. This is the contract the
    # E2E probe exposed as broken.
    assert c.proposal_provider == "anthropic", (
        f"proposal_provider must survive downstream validation failure; "
        f"got {c.proposal_provider!r} with failure_reason={c.failure_reason!r}"
    )


def test_propose_patch_does_not_apply(db):
    """Patch proposal does NOT apply any code changes."""
    c = BugFixCandidate(
        source_type="manual", source_ref="test2",
        title="Test", summary="test", status="open",
    )
    db.add(c)
    db.flush()

    mock_response = json.dumps({
        "patch_summary": "Fix",
        "files": ["tests/test_mock_noapply.py"],
        "diff": "--- /dev/null\n+++ b/tests/test_mock_noapply.py\n@@ -0,0 +1 @@\n+# test\n",
        "test_command": "pytest",
    })

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "anthropic", "claude-sonnet-4-6"),
    ):
        propose_patch(db, c.id)

    # Status is proposed, NOT applied
    db.refresh(c)
    assert c.status == "patch_proposed"
    assert c.applied_at is None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_list_bugfixes(client, db):
    """GET /ops/bugfixes returns candidates."""
    db.add(BugFixCandidate(
        source_type="manual", source_ref="api_test",
        title="API test bug", status="open",
    ))
    db.flush()
    db.commit()

    resp = client.get("/ops/bugfixes", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(c["title"] == "API test bug" for c in data)


def test_get_bugfix_detail(client, db):
    """GET /ops/bugfixes/{id} returns full details."""
    c = BugFixCandidate(
        source_type="manual", source_ref="detail_test",
        title="Detail test", summary="Details here",
        patch_diff="diff content", status="patch_proposed",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.get(f"/ops/bugfixes/{c.id}", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Detail test"
    assert data["patch_diff"] == "diff content"


def test_approve_bugfix(client, db):
    """POST /ops/bugfixes/{id}/approve → approved status."""
    c = BugFixCandidate(
        source_type="manual", source_ref="approve_test",
        title="Approve test", status="patch_proposed",
        patch_diff="diff", patch_summary="fix",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.post(f"/ops/bugfixes/{c.id}/approve", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_reject_bugfix(client, db):
    """POST /ops/bugfixes/{id}/reject → rejected status."""
    c = BugFixCandidate(
        source_type="manual", source_ref="reject_test",
        title="Reject test", status="open",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.post(f"/ops/bugfixes/{c.id}/reject", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_bugfixes_require_auth(client):
    """All bugfix endpoints require operator auth."""
    assert client.get("/ops/bugfixes").status_code == 401
    assert client.get("/ops/bugfixes/1").status_code == 401


def test_double_approve_blocked(client, db):
    """Cannot approve an already-approved candidate."""
    c = BugFixCandidate(
        source_type="manual", source_ref="double_test",
        title="Double test", status="approved",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.post(f"/ops/bugfixes/{c.id}/approve", headers=_op_headers())
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Slack notification resilience
# ---------------------------------------------------------------------------

def test_slack_failure_does_not_break_proposal(db):
    """Slack send failure during proposal does not crash or lose data."""
    c = BugFixCandidate(
        source_type="manual", source_ref="slack_fail",
        title="Slack fail test", status="open",
    )
    db.add(c)
    db.flush()

    mock_response = json.dumps({
        "patch_summary": "Fix",
        "files": ["tests/test_mock_slack.py"],
        "diff": "--- /dev/null\n+++ b/tests/test_mock_slack.py\n@@ -0,0 +1 @@\n+# test\n",
        "test_command": "",
    })

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "anthropic", "claude-sonnet-4-6"),
    ), \
         patch("app.core.alert_delivery._SLACK_URL", "https://fake"), \
         patch("app.core.alert_delivery.httpx.post", side_effect=Exception("slack down")):
        result = propose_patch(db, c.id)

    assert result is True
    db.refresh(c)
    assert c.status == "patch_proposed"
    assert c.notified_at is None  # Slack failed but candidate persists
