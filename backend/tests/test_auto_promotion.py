"""Tests for full auto-promotion pipeline (TIER_0)."""
import os
import time
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.autofix_promotion import AutoFixPromotion
from app.services.promotion_pipeline import (
    run_auto_promotion,
    is_promotion_ready,
    _is_push_on_cooldown,
    _set_push_cooldown,
    _auto_push_cooldown,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_promo(db, status="pending", sha="auto_sha_test"):
    p = AutoFixPromotion(
        bugfix_candidate_id=5000,
        git_commit_sha=sha,
        status=status,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def test_readiness_reports_missing_infra():
    """No remote/gh → not ready with reasons."""
    with patch("app.services.promotion_pipeline._has_remote", return_value=False), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
        ready, reasons = is_promotion_ready()
    assert ready is False
    assert "no_git_remote" in reasons
    assert "no_gh_cli" in reasons


def test_readiness_reports_ready():
    """With remote + gh → ready."""
    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=True):
        ready, reasons = is_promotion_ready()
    assert ready is True
    assert len(reasons) == 0


# ---------------------------------------------------------------------------
# Auto-branch
# ---------------------------------------------------------------------------

def test_auto_promotion_branches_pending(db):
    """Pending promotion gets branch created automatically."""
    promo = _make_promo(db, status="pending")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=1),  # branch doesn't exist
            MagicMock(returncode=0, stderr=""),  # git branch created
        ]
        with patch("app.services.promotion_pipeline._has_remote", return_value=False), \
             patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
            summary = run_auto_promotion(db)

    assert summary["branched"] >= 1
    db.refresh(promo)
    assert promo.status == "branch_created"


# ---------------------------------------------------------------------------
# Auto-push
# ---------------------------------------------------------------------------

def test_auto_push_when_ready(db):
    """branch_created promotion gets pushed when infra ready."""
    promo = _make_promo(db, status="branch_created")
    promo.branch_name = "autofix/test"
    db.flush()
    _auto_push_cooldown.clear()

    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
        summary = run_auto_promotion(db)

    assert summary["pushed"] >= 1


def test_auto_push_skips_when_no_remote(db):
    """No remote → push skipped, no error."""
    promo = _make_promo(db, status="branch_created")
    promo.branch_name = "autofix/test"
    db.flush()

    with patch("app.services.promotion_pipeline._has_remote", return_value=False), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
        summary = run_auto_promotion(db)

    assert summary["pushed"] == 0
    db.refresh(promo)
    assert promo.status == "branch_created"  # unchanged


def test_auto_push_cooldown_enforced(db):
    """Same candidate within cooldown → skipped."""
    promo = _make_promo(db, status="branch_created")
    promo.branch_name = "autofix/test"
    db.flush()
    _set_push_cooldown(promo.bugfix_candidate_id)

    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=True):
        summary = run_auto_promotion(db)

    assert summary["skipped"] >= 1
    assert summary["pushed"] == 0
    _auto_push_cooldown.clear()


def test_auto_push_max_per_cycle(db):
    """Max 1 push per cycle."""
    for i in range(3):
        p = AutoFixPromotion(
            bugfix_candidate_id=6000 + i, git_commit_sha=f"sha_{i}",
            status="branch_created", branch_name=f"autofix/test_{i}",
        )
        db.add(p)
    db.flush()
    _auto_push_cooldown.clear()

    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
        summary = run_auto_promotion(db, max_per_cycle=1)

    assert summary["pushed"] == 1
    _auto_push_cooldown.clear()


# ---------------------------------------------------------------------------
# Remote CI polling
# ---------------------------------------------------------------------------

def test_ci_polling_updates_status(db):
    """Pushed promotion gets CI polled via direct call."""
    from app.services.promotion_pipeline import check_remote_ci_status
    promo = _make_promo(db, status="pushed")
    promo.pushed_at = _now()
    promo.branch_name = "autofix/test"
    db.flush()

    gh_output = json.dumps([{"status": "completed", "conclusion": "success", "url": "https://ci/1"}])
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=gh_output, stderr="")):
        result = check_remote_ci_status(db, promo.id)

    assert result == "passed"
    assert promo.remote_ci_status == "passed"


def test_ci_failure_creates_alert_via_promotion(db):
    """Remote CI failure from direct call stores failed status."""
    from app.services.promotion_pipeline import check_remote_ci_status
    promo = _make_promo(db, status="pushed")
    promo.pushed_at = _now()
    promo.branch_name = "autofix/test"
    db.flush()

    gh_output = json.dumps([{"status": "completed", "conclusion": "failure", "url": "https://ci/2"}])
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=gh_output, stderr="")):
        result = check_remote_ci_status(db, promo.id)

    assert result == "failed"
    assert promo.remote_ci_status == "failed"


# ---------------------------------------------------------------------------
# PR auto-creation
# ---------------------------------------------------------------------------

def test_pr_auto_created_via_direct_call(db):
    """PR creation works via direct call when CI passed."""
    from app.services.promotion_pipeline import create_promotion_pr
    promo = _make_promo(db, status="pushed")
    promo.pushed_at = _now()
    promo.branch_name = "autofix/test"
    promo.remote_ci_status = "passed"
    db.flush()

    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/100\n", stderr="")):
        result = create_promotion_pr(db, promo.id)

    assert result.startswith("https://")
    assert promo.pr_url is not None


# ---------------------------------------------------------------------------
# No auto-merge
# ---------------------------------------------------------------------------

def test_no_auto_merge(db):
    """Auto-promotion never calls merge."""
    promo = _make_promo(db, status="pushed")
    promo.pushed_at = _now()
    promo.branch_name = "autofix/test"
    promo.pr_url = "https://github.com/pr/1"
    promo.pr_number = 1
    promo.remote_ci_status = "passed"
    db.flush()

    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("app.services.promotion_pipeline._has_gh_cli", return_value=True):
        summary = run_auto_promotion(db)

    # Status should NOT be merged
    db.refresh(promo)
    assert promo.status != "merged"


# ---------------------------------------------------------------------------
# Worker integration
# ---------------------------------------------------------------------------

def test_agent_worker_has_promotion_phase():
    """agent_worker contains auto-promotion phase."""
    import inspect
    from app.workers.agent_worker import _run_bug_triage
    source = inspect.getsource(_run_bug_triage)
    assert "run_auto_promotion" in source


# ---------------------------------------------------------------------------
# Readiness API
# ---------------------------------------------------------------------------

def test_readiness_includes_promotion_stats(client):
    """Readiness endpoint includes promotion stats."""
    resp = client.get("/ops/readiness/orchestrator", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "promotion" in data
    assert "auto_promotion_ready" in data["promotion"]
    assert "stats" in data["promotion"]
