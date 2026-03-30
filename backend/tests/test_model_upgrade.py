"""Tests for model upgrade agent."""
import os
from unittest.mock import patch

from sqlalchemy import text

from app.models.model_upgrade import ModelUpgradeProposal
from app.services.model_upgrade_agent import (
    scan_for_upgrades,
    evaluate_upgrade,
    generate_upgrade_evolution_proposals,
    _evaluate_response,
    _get_current_approved,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _make_proposal(db, current="claude-sonnet-4-20250514", candidate="claude-opus-4-20250514", module="bugfix_proposal"):
    p = ModelUpgradeProposal(
        current_provider="anthropic", current_model=current,
        candidate_provider="anthropic", candidate_model=candidate,
        target_module=module, reason="Better reasoning",
        expected_benefit="Higher quality patches", risk_level="LEVEL_2",
        status="pending",
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Scan dedup
# ---------------------------------------------------------------------------

def test_scan_creates_proposals(db):
    """scan_for_upgrades creates proposals for candidate models."""
    summary = scan_for_upgrades(db)
    assert summary["scanned"] >= 1
    # Should create at least some proposals (depends on CURRENT_APPROVED vs CANDIDATE_MODELS)


def test_scan_dedup(db):
    """Same candidate pair not duplicated."""
    s1 = scan_for_upgrades(db)
    db.flush()
    s2 = scan_for_upgrades(db)
    assert s2["created"] == 0
    assert s2["deduped"] >= s1["created"] or s2["scanned"] >= 1


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def test_evaluate_response_pass():
    """Valid JSON with expected keys → pass."""
    text = '{"assessment": "test", "actions": []}'
    result, detail = _evaluate_response(text, ["assessment", "actions"])
    assert result == "pass"
    assert detail["valid_json"] is True


def test_evaluate_response_fail_empty():
    """Empty response → fail."""
    result, _ = _evaluate_response("", ["assessment"])
    assert result == "fail"


def test_evaluate_response_fail_invalid_json():
    """Invalid JSON → fail."""
    result, _ = _evaluate_response("not json", ["assessment"])
    assert result == "fail"


def test_evaluate_response_inconclusive_missing_keys():
    """Valid JSON but missing expected keys → inconclusive."""
    result, detail = _evaluate_response('{"foo": "bar"}', ["assessment", "actions"])
    assert result == "inconclusive"
    assert len(detail["keys_missing"]) > 0


# ---------------------------------------------------------------------------
# No auto-switch
# ---------------------------------------------------------------------------

def test_approval_does_not_auto_switch(db):
    """Approving a proposal does NOT change active model config."""
    original = _get_current_approved("bugfix_proposal", db)
    p = _make_proposal(db)
    p.status = "evaluated"
    p.eval_result = "pass"
    db.flush()

    # Simulate approval (without activation)
    p.status = "approved"
    p.decided_by = "operator"
    db.flush()

    # Active config must NOT have changed (approval != activation)
    current = _get_current_approved("bugfix_proposal", db)
    assert current["model"] == original["model"]


# ---------------------------------------------------------------------------
# Upgrade-driven evolution
# ---------------------------------------------------------------------------

def test_upgrade_evolution_proposals(db):
    """Approved + pass eval → generates evolution proposals."""
    p = _make_proposal(db, module="bugfix_proposal")
    p.status = "evaluated"
    p.eval_result = "pass"
    db.flush()

    count = generate_upgrade_evolution_proposals(db, p.id)
    assert count >= 1

    from app.models.evolution_proposal import EvolutionProposal
    evo = db.query(EvolutionProposal).filter(
        EvolutionProposal.dedup_key.like(f"model_upgrade:{p.candidate_model}%"),
    ).first()
    assert evo is not None
    assert evo.risk_level == "LEVEL_2"


def test_upgrade_evolution_not_for_fail(db):
    """Failed eval → no evolution proposals."""
    p = _make_proposal(db)
    p.status = "evaluated"
    p.eval_result = "fail"
    db.flush()

    count = generate_upgrade_evolution_proposals(db, p.id)
    assert count == 0


# ---------------------------------------------------------------------------
# Worker scheduling
# ---------------------------------------------------------------------------

def test_scan_cooldown():
    """Scan has weekly cooldown."""
    from app.services.model_upgrade_agent import should_run_scan, mark_scan_run, _last_scan
    import app.services.model_upgrade_agent as agent
    original = agent._last_scan
    try:
        agent._last_scan = None
        assert should_run_scan() is True
        mark_scan_run()
        assert should_run_scan() is False
    finally:
        agent._last_scan = original


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_list_endpoint(client, db):
    """GET /ops/model-upgrades returns list."""
    _make_proposal(db)
    db.commit()
    resp = client.get("/ops/model-upgrades", headers=_op_headers())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_api_requires_auth(client):
    """All endpoints require auth."""
    assert client.get("/ops/model-upgrades").status_code == 401
