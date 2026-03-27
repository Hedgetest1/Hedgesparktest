"""Tests for remote CI monitoring, PR creation, and merge gating."""
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import json

from app.models.autofix_promotion import AutoFixPromotion
from app.services.promotion_pipeline import (
    check_remote_ci_status,
    create_promotion_pr,
    merge_promotion,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_pushed_promo(db, sha="pushed_sha_123"):
    p = AutoFixPromotion(
        bugfix_candidate_id=1000, git_commit_sha=sha,
        branch_name="autofix/candidate-1000-pushed_s",
        status="pushed", pushed_at=_now(),
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Remote CI
# ---------------------------------------------------------------------------

def test_remote_ci_unconfigured(db):
    """No gh CLI → remote_ci_status = unconfigured."""
    promo = _make_pushed_promo(db)
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
        result = check_remote_ci_status(db, promo.id)
    assert result == "unconfigured"
    assert promo.remote_ci_status == "unconfigured"


def test_remote_ci_passed(db):
    """gh reports success → passed."""
    promo = _make_pushed_promo(db)

    gh_output = json.dumps([{"status": "completed", "conclusion": "success", "url": "https://github.com/runs/1"}])
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=gh_output, stderr="")):
        result = check_remote_ci_status(db, promo.id)

    assert result == "passed"
    assert promo.remote_ci_status == "passed"
    assert promo.remote_ci_url == "https://github.com/runs/1"


def test_remote_ci_failed(db):
    """gh reports failure → failed."""
    promo = _make_pushed_promo(db)

    gh_output = json.dumps([{"status": "completed", "conclusion": "failure", "url": "https://github.com/runs/2"}])
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=gh_output, stderr="")):
        result = check_remote_ci_status(db, promo.id)

    assert result == "failed"


def test_remote_ci_no_fake_pass(db):
    """No runs found → unknown, NOT fake-passed."""
    promo = _make_pushed_promo(db)

    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="[]", stderr="")):
        result = check_remote_ci_status(db, promo.id)

    assert result == "unknown"
    assert promo.remote_ci_status == "unknown"


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------

def test_pr_blocked_when_not_pushed(db):
    """PR creation blocked when branch not pushed."""
    p = AutoFixPromotion(
        bugfix_candidate_id=1001, git_commit_sha="sha",
        branch_name="autofix/test", status="branch_created",
    )
    db.add(p)
    db.flush()

    result = create_promotion_pr(db, p.id)
    assert "not_pushed" in result


def test_pr_blocked_no_gh_cli(db):
    """PR creation fails when gh CLI not available."""
    promo = _make_pushed_promo(db)
    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
        result = create_promotion_pr(db, promo.id)
    assert "gh_cli" in result


def test_pr_creation_success(db):
    """Successful PR creation stores URL and number."""
    promo = _make_pushed_promo(db)

    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="https://github.com/org/repo/pull/42\n", stderr="")):
        result = create_promotion_pr(db, promo.id)

    assert result.startswith("https://")
    assert promo.pr_url == "https://github.com/org/repo/pull/42"
    assert promo.pr_number == 42


# ---------------------------------------------------------------------------
# Merge gating
# ---------------------------------------------------------------------------

def test_merge_blocked_no_pr(db):
    """Merge blocked when no PR exists."""
    promo = _make_pushed_promo(db)
    result = merge_promotion(db, promo.id)
    assert "no_pr" in result


def test_merge_blocked_ci_not_passed(db):
    """Merge blocked when remote CI not passed."""
    promo = _make_pushed_promo(db)
    promo.pr_url = "https://github.com/pr/1"
    promo.pr_number = 1
    promo.remote_ci_status = "failed"
    db.flush()

    result = merge_promotion(db, promo.id)
    assert "ci_not_passed" in result


def test_merge_success(db):
    """Merge succeeds when CI passed + PR exists."""
    promo = _make_pushed_promo(db)
    promo.pr_url = "https://github.com/pr/1"
    promo.pr_number = 1
    promo.remote_ci_status = "passed"
    promo.status = "approved"
    db.flush()

    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=True), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="merged\nabc123sha", stderr="")
        result = merge_promotion(db, promo.id)

    assert result == "merged"
    assert promo.status == "merged"
    assert promo.merged_at is not None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_remote_ci_endpoint(client, db):
    """GET /ops/promotions/{id}/remote-ci returns status."""
    promo = _make_pushed_promo(db)
    db.commit()

    with patch("app.services.promotion_pipeline._has_gh_cli", return_value=False):
        resp = client.get(f"/ops/promotions/{promo.id}/remote-ci", headers=_op_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "unconfigured"


def test_pr_endpoint_requires_auth(client):
    """PR creation endpoint requires auth."""
    resp = client.post("/ops/promotions/1/pr", headers={"Content-Type": "application/json"})
    assert resp.status_code == 401


def test_merge_endpoint_requires_auth(client):
    """Merge endpoint requires auth."""
    resp = client.post("/ops/promotions/1/merge", headers={"Content-Type": "application/json"})
    assert resp.status_code == 401


def test_detail_exposes_pr_fields(client, db):
    """GET detail includes pr_url, remote_ci_status, merged_at."""
    promo = _make_pushed_promo(db)
    promo.pr_url = "https://github.com/pr/99"
    promo.pr_number = 99
    promo.remote_ci_status = "passed"
    db.flush()
    db.commit()

    resp = client.get(f"/ops/promotions/{promo.id}", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["pr_url"] == "https://github.com/pr/99"
    assert data["pr_number"] == 99
    assert data["remote_ci_status"] == "passed"
