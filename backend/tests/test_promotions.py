"""Tests for autofix promotion pipeline."""
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.autofix_promotion import AutoFixPromotion
from app.services.promotion_pipeline import (
    create_promotion,
    create_promotion_branch,
    run_promotion_ci_check,
    push_promotion,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_promo(db, status="pending", sha="abc123def456"):
    p = AutoFixPromotion(
        bugfix_candidate_id=999,
        git_commit_sha=sha,
        status=status,
    )
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Promotion creation
# ---------------------------------------------------------------------------

def test_create_promotion(db):
    """create_promotion creates a row with pending status."""
    promo = create_promotion(db, bugfix_candidate_id=1, git_commit_sha="sha123")
    assert promo.id is not None
    assert promo.status == "pending"
    assert promo.bugfix_candidate_id == 1


def test_create_promotion_dedup(db):
    """Duplicate creation returns existing row."""
    p1 = create_promotion(db, bugfix_candidate_id=2, git_commit_sha="sha1")
    p2 = create_promotion(db, bugfix_candidate_id=2, git_commit_sha="sha2")
    assert p1.id == p2.id


# ---------------------------------------------------------------------------
# Branch creation
# ---------------------------------------------------------------------------

def test_branch_name_deterministic(db):
    """Branch name follows autofix/candidate-{id}-{shortsha} pattern."""
    promo = _make_promo(db)

    with patch("subprocess.run") as mock_run:
        # rev-parse --verify → branch doesn't exist
        mock_run.side_effect = [
            MagicMock(returncode=1),  # branch doesn't exist
            MagicMock(returncode=0, stderr=""),  # git branch created
        ]
        result = create_promotion_branch(db, promo.id)

    assert result.startswith("autofix/candidate-999-")
    assert promo.status == "branch_created"


def test_duplicate_branch_reuses(db):
    """If branch already exists, status is set without error."""
    promo = _make_promo(db)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)  # branch exists
        result = create_promotion_branch(db, promo.id)

    assert "autofix/" in result
    assert promo.status == "branch_created"


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

def test_ci_check_local_pass(db):
    """Local pytest pass → ci_passed."""
    promo = _make_promo(db, status="branch_created")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="passed", stderr="")
        result = run_promotion_ci_check(db, promo.id)

    assert result == "ci_passed"
    assert promo.status == "ci_passed"


def test_ci_check_local_fail(db):
    """Local pytest fail → ci_failed."""
    promo = _make_promo(db, status="branch_created")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="")
        result = run_promotion_ci_check(db, promo.id)

    assert result == "ci_failed"
    assert promo.status == "ci_failed"


# ---------------------------------------------------------------------------
# Push gating
# ---------------------------------------------------------------------------

def test_push_blocked_wrong_status(db):
    """Push blocked when status is not approved/ci_passed."""
    promo = _make_promo(db, status="pending")
    result = push_promotion(db, promo.id)
    assert "wrong_status" in result


def test_push_blocked_no_remote(db):
    """Push blocked when no git remote configured."""
    promo = _make_promo(db, status="approved")
    promo.branch_name = "autofix/test"
    db.flush()

    with patch("app.services.promotion_pipeline._has_remote", return_value=False):
        result = push_promotion(db, promo.id)

    assert result == "no_remote"
    assert promo.status == "failed"


def test_push_success(db):
    """Push succeeds → status=pushed."""
    promo = _make_promo(db, status="approved")
    promo.branch_name = "autofix/test"
    db.flush()

    with patch("app.services.promotion_pipeline._has_remote", return_value=True), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
        result = push_promotion(db, promo.id)

    assert result == "pushed"
    assert promo.status == "pushed"
    assert promo.pushed_at is not None


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

def test_list_promotions(client, db):
    """GET /ops/promotions returns list."""
    _make_promo(db)
    db.commit()
    resp = client.get("/ops/promotions", headers=_op_headers())
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_promotions_require_auth(client):
    """All promotion endpoints require auth."""
    assert client.get("/ops/promotions").status_code == 401


def test_approve_promotion(client, db):
    """POST approve changes status."""
    promo = _make_promo(db, status="ci_passed")
    db.commit()
    resp = client.post(f"/ops/promotions/{promo.id}/approve", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_reject_promotion(client, db):
    """POST reject changes status."""
    promo = _make_promo(db)
    db.commit()
    resp = client.post(f"/ops/promotions/{promo.id}/reject", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# Slack resilience
# ---------------------------------------------------------------------------

def test_slack_failure_does_not_break_creation(db):
    """Slack send failure does not prevent promotion creation."""
    with patch("app.services.promotion_pipeline._notify_promotion", side_effect=Exception("slack down")):
        # Should not raise even if notification fails
        promo = create_promotion(db, bugfix_candidate_id=88, git_commit_sha="test_sha")
    # Verify promo was created despite notification failure
    assert promo.id is not None
