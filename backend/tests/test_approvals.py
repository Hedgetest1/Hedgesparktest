"""Tests for TIER_1 human approval + execution flow."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.models.action_approval import ActionApproval
from app.models.ops_alert import OpsAlert
from app.services.audit import write_audit_log
from app.services.orchestrator import (
    ACTION_REGISTRY, TIER_1, _clear_cooldowns,
    run_orchestrator_cycle,
)
from app.services.orchestrator_llm import LLMDecisionResult, LLMProposal

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_pending_approval(db, action="restart_worker", target="wishspark-worker"):
    audit = write_audit_log(
        db, actor_type="agent", actor_name="orchestrator_claude",
        action_type=f"llm_propose_{action}", target_id=target,
        status="awaiting_approval",
    )
    db.flush()
    a = ActionApproval(
        audit_log_id=audit.id, action_type=action, target_id=target,
        status="pending", expires_at=_now() + timedelta(hours=2),
    )
    db.add(a)
    db.flush()
    return a


# ---------------------------------------------------------------------------
# Proposal → Approval creation
# ---------------------------------------------------------------------------

def test_hybrid_tier1_creates_approval(db, merchant_a):
    """TIER_1 proposal in hybrid mode creates an ActionApproval row."""
    _clear_cooldowns()
    import app.services.orchestrator as orch
    original = orch.ORCHESTRATOR_MODE
    try:
        orch.ORCHESTRATOR_MODE = "hybrid"

        mock_result = LLMDecisionResult(
            assessment="Worker stuck", model_used="test", raw_response="{}",
        )
        mock_result.proposals = [
            LLMProposal(action="restart_worker", target="wishspark-worker", reason="stuck", valid=True),
        ]

        with patch("app.services.orchestrator_context.build_orchestrator_context", return_value="ctx"), \
             patch("app.services.orchestrator_llm.claude_decision", return_value=mock_result):
            run_orchestrator_cycle(db)

        # Check approval was created
        approval = db.execute(text(
            "SELECT action_type, status FROM action_approvals WHERE action_type = 'restart_worker' ORDER BY id DESC LIMIT 1"
        )).fetchone()
        assert approval is not None
        assert approval[1] == "pending"

    finally:
        orch.ORCHESTRATOR_MODE = original


# ---------------------------------------------------------------------------
# Approve → Execute
# ---------------------------------------------------------------------------

def test_approve_executes_action(client, db):
    """Approving a pending approval executes the action."""
    _clear_cooldowns()
    approval = _make_pending_approval(db, action="resolve_alert", target="999")

    # Create a fake alert to resolve
    alert = OpsAlert(
        severity="info", source="test", alert_type="test_approve",
        summary="test", created_at=_now(),
    )
    db.add(alert)
    db.flush()
    # Update approval target to real alert id
    approval.target_id = str(alert.id)
    db.commit()

    resp = client.post(f"/ops/approvals/{approval.id}/approve", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved_and_executed"
    assert "resolved" in data["result"]

    # Check approval status updated
    db.expire_all()
    assert approval.status == "approved"


def test_approve_writes_audit_log(client, db):
    """Approved action writes audit_log with actor=human_approval."""
    _clear_cooldowns()
    alert = OpsAlert(severity="info", source="test", alert_type="t", summary="s", created_at=_now())
    db.add(alert)
    db.flush()
    approval = _make_pending_approval(db, action="resolve_alert", target=str(alert.id))
    db.commit()

    client.post(f"/ops/approvals/{approval.id}/approve", headers=_op_headers())

    audit = db.execute(text(
        "SELECT actor_name, approval_mode FROM audit_log WHERE actor_name = 'human_approval' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "human_approved"


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

def test_reject_does_not_execute(client, db):
    """Rejecting a pending approval does NOT execute the action."""
    _clear_cooldowns()
    approval = _make_pending_approval(db)
    db.commit()

    resp = client.post(f"/ops/approvals/{approval.id}/reject", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# Expiration
# ---------------------------------------------------------------------------

def test_expired_cannot_execute(client, db):
    """Expired approval returns 410."""
    _clear_cooldowns()
    audit = write_audit_log(
        db, actor_type="agent", actor_name="test", action_type="test", status="proposed",
    )
    db.flush()
    approval = ActionApproval(
        audit_log_id=audit.id, action_type="restart_worker", target_id="wishspark-worker",
        status="pending", expires_at=_now() - timedelta(hours=1),  # already expired
    )
    db.add(approval)
    db.flush()
    db.commit()

    resp = client.post(f"/ops/approvals/{approval.id}/approve", headers=_op_headers())
    assert resp.status_code == 410


# ---------------------------------------------------------------------------
# Double approve
# ---------------------------------------------------------------------------

def test_double_approve_blocked(client, db):
    """Approving an already-approved action returns 409."""
    _clear_cooldowns()
    alert = OpsAlert(severity="info", source="test", alert_type="t", summary="s", created_at=_now())
    db.add(alert)
    db.flush()
    approval = _make_pending_approval(db, action="resolve_alert", target=str(alert.id))
    db.commit()

    resp1 = client.post(f"/ops/approvals/{approval.id}/approve", headers=_op_headers())
    assert resp1.status_code == 200

    resp2 = client.post(f"/ops/approvals/{approval.id}/approve", headers=_op_headers())
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_approvals_require_auth(client, db):
    """All approval endpoints require operator auth."""
    assert client.get("/ops/approvals").status_code == 401
    assert client.post("/ops/approvals/1/approve", headers={"Content-Type": "application/json"}).status_code == 401
    assert client.post("/ops/approvals/1/reject", headers={"Content-Type": "application/json"}).status_code == 401


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_pending_approvals(client, db):
    """GET /ops/approvals returns pending approvals."""
    _make_pending_approval(db)
    db.commit()

    resp = client.get("/ops/approvals", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(a["action_type"] == "restart_worker" for a in data)
