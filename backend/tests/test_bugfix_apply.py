"""Tests for bugfix apply pipeline — safety checks, apply, rollback."""
import json
import os
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    apply_bugfix_candidate,
    _check_forbidden_paths,
    _FORBIDDEN_PATH_PATTERNS,
    ApplyResult,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_approved(db, diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", files=None):
    c = BugFixCandidate(
        source_type="manual", source_ref="apply_test",
        title="Apply test", summary="test", status="approved",
        patch_diff=diff,
        patch_files=json.dumps(files or ["app/services/test_file.py"]),
        test_command=f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/venv/bin/python -m pytest tests/test_encryption.py -q",
    )
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Forbidden path blocklist
# ---------------------------------------------------------------------------

def test_forbidden_path_blocks_apply():
    """Patch touching token_crypto → rejected."""
    result = _check_forbidden_paths(json.dumps(["app/core/token_crypto.py"]))
    assert result is not None
    assert "forbidden" in result


def test_forbidden_billing_blocked():
    """Patch touching billing → rejected."""
    result = _check_forbidden_paths(json.dumps(["app/api/billing.py"]))
    assert result is not None


def test_forbidden_migrations_blocked():
    """Patch touching migrations → rejected."""
    result = _check_forbidden_paths(json.dumps(["migrations/versions/new.py"]))
    assert result is not None


def test_safe_path_allowed():
    """Patch touching normal service file → allowed."""
    result = _check_forbidden_paths(json.dumps(["app/services/signal_text.py"]))
    assert result is None


def test_forbidden_paths_in_apply(db):
    """apply_bugfix_candidate rejects forbidden paths (via guard or legacy check)."""
    c = _make_approved(db, files=["app/core/token_crypto.py"])
    result = apply_bugfix_candidate(db, c.id)
    assert result.status == "apply_failed"
    assert "forbidden" in result.failure_reason or "TIER_2" in result.failure_reason


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def test_wrong_status_blocks_apply(db):
    """Only approved candidates can be applied."""
    c = BugFixCandidate(
        source_type="manual", source_ref="status_test",
        title="test", status="open", patch_diff="diff",
    )
    db.add(c)
    db.flush()
    result = apply_bugfix_candidate(db, c.id)
    assert result.status == "apply_failed"
    assert "wrong_status" in result.failure_reason


def test_empty_diff_blocks_apply(db):
    """Empty patch_diff → apply_failed."""
    c = BugFixCandidate(
        source_type="manual", source_ref="empty_test",
        title="test", status="approved", patch_diff="",
    )
    db.add(c)
    db.flush()
    result = apply_bugfix_candidate(db, c.id)
    assert result.status == "apply_failed"
    assert "empty" in result.failure_reason


# ---------------------------------------------------------------------------
# Git tree dirty check
# ---------------------------------------------------------------------------

def test_dirty_git_tree_blocks_apply(db):
    """Dirty working tree → apply_failed."""
    c = _make_approved(db)
    with patch("subprocess.run") as mock_run:
        # git diff --quiet returns 1 (dirty)
        mock_run.return_value = MagicMock(returncode=1, stderr="", stdout="")
        result = apply_bugfix_candidate(db, c.id)
    assert result.status == "apply_failed"
    assert "dirty" in result.failure_reason


# ---------------------------------------------------------------------------
# Apply --check failure
# ---------------------------------------------------------------------------

def test_apply_check_failure_blocks(db):
    """git apply --check failure → apply_failed."""
    from tests.conftest import make_git_safe_subprocess_mock
    c = _make_approved(db)

    with patch(
        "subprocess.run",
        side_effect=make_git_safe_subprocess_mock(apply_check_returncode=1),
    ):
        result = apply_bugfix_candidate(db, c.id)
    assert result.status == "apply_failed"
    assert "check" in (result.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# Failed tests trigger rollback
# ---------------------------------------------------------------------------

def test_failed_tests_trigger_rollback(db):
    """Tests fail after apply → rolled_back."""
    from tests.conftest import make_git_safe_subprocess_mock
    c = _make_approved(db)

    with patch(
        "subprocess.run",
        side_effect=make_git_safe_subprocess_mock(pytest_returncode=1),
    ):
        result = apply_bugfix_candidate(db, c.id)

    assert result.status == "rolled_back"
    assert "tests_failed" in (result.failure_reason or "")
    db.refresh(c)
    assert c.status == "rolled_back"


# ---------------------------------------------------------------------------
# Apply API endpoint
# ---------------------------------------------------------------------------

def test_apply_endpoint_requires_auth(client, db):
    """POST /ops/bugfixes/{id}/apply requires operator auth."""
    resp = client.post("/ops/bugfixes/999/apply", headers={"Content-Type": "application/json"})
    assert resp.status_code == 401


def test_apply_endpoint_rejects_non_approved(client, db):
    """Apply endpoint rejects non-approved candidate."""
    c = BugFixCandidate(
        source_type="manual", source_ref="api_apply",
        title="test", status="open", patch_diff="d",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.post(f"/ops/bugfixes/{c.id}/apply", headers=_op_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "apply_failed"


# ---------------------------------------------------------------------------
# Audit + alert on failure
# ---------------------------------------------------------------------------

def test_rollback_creates_alert(db):
    """Rollback writes a critical ops_alert."""
    c = _make_approved(db)

    def _mock_run(cmd, **kwargs):
        m = MagicMock()
        m.stdout = ""
        m.stderr = ""
        if "diff" in cmd and "--quiet" in cmd:
            m.returncode = 0
        elif "--check" in cmd:
            m.returncode = 0
        elif "apply" in cmd and "-R" not in cmd and "--check" not in cmd:
            m.returncode = 0
        elif "pytest" in " ".join(cmd):
            m.returncode = 1
        elif "-R" in cmd:
            m.returncode = 0
        else:
            m.returncode = 0
        return m

    with patch("subprocess.run", side_effect=_mock_run):
        apply_bugfix_candidate(db, c.id)

    alert = db.execute(text(
        "SELECT alert_type, severity FROM ops_alerts WHERE alert_type = 'bugfix_rolled_back' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert alert is not None
    assert alert[1] == "critical"


# ---------------------------------------------------------------------------
# Auto-triage wiring
# ---------------------------------------------------------------------------

def test_agent_worker_has_triage_phase():
    """agent_worker has _run_bug_triage function."""
    from app.workers.agent_worker import _run_bug_triage
    assert callable(_run_bug_triage)


# ---------------------------------------------------------------------------
# Frontend build enforcement
# ---------------------------------------------------------------------------

def test_frontend_change_requires_build(db):
    """Patch touching dashboard fails if frontend build fails."""
    c = _make_approved(db, files=["dashboard/src/App.tsx"])

    call_count = [0]
    def _mock_run(cmd, **kwargs):
        m = MagicMock()
        call_count[0] += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "diff" in cmd_str and "--quiet" in cmd_str:
            m.returncode = 0
        elif "--check" in cmd_str:
            m.returncode = 0
        elif "apply" in cmd_str and "-R" not in cmd_str:
            m.returncode = 0
        elif "pytest" in cmd_str:
            m.returncode = 0
            m.stdout = "10 passed"
            m.stderr = ""
        elif "-R" in cmd_str:
            m.returncode = 0
        elif "next" in cmd_str and "build" in cmd_str:
            m.returncode = 1  # build fails
            m.stdout = "Build error"
            m.stderr = "Module not found"
        else:
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
        return m

    with patch("subprocess.run", side_effect=_mock_run):
        result = apply_bugfix_candidate(db, c.id)
    assert result.status == "rolled_back"
    assert "frontend_build" in result.failure_reason


# ---------------------------------------------------------------------------
# Tracker version bump enforcement
# ---------------------------------------------------------------------------

def test_tracker_change_without_version_bump_fails(db):
    """Patch touching tracker JS without TRACKER_VERSION bump → rolled_back."""
    c = _make_approved(
        db,
        files=["tracker/spark-tracker.js"],
        diff="--- a/tracker/spark-tracker.js\n+++ b/tracker/spark-tracker.js\n@@ -1 +1 @@\n-old\n+new",
    )

    call_count = [0]
    def _mock_run(cmd, **kwargs):
        m = MagicMock()
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "diff" in cmd_str and "--quiet" in cmd_str:
            m.returncode = 0
        elif "--check" in cmd_str:
            m.returncode = 0
        elif "apply" in cmd_str and "-R" not in cmd_str:
            m.returncode = 0
        elif "pytest" in cmd_str:
            m.returncode = 0
            m.stdout = "10 passed"
            m.stderr = ""
        elif "-R" in cmd_str:
            m.returncode = 0
        else:
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
        return m

    with patch("subprocess.run", side_effect=_mock_run):
        result = apply_bugfix_candidate(db, c.id)
    assert result.status == "rolled_back"
    assert "tracker_version" in result.failure_reason.lower()


# ---------------------------------------------------------------------------
# Subprocess timeout rollback
# ---------------------------------------------------------------------------

def test_test_timeout_triggers_rollback(db):
    """TimeoutExpired during test run must trigger rollback, not leave corrupted state."""
    c = _make_approved(db)

    call_count = {"n": 0}

    def _mock_run(cmd, **kwargs):
        call_count["n"] += 1
        m = MagicMock()
        m.stdout = ""
        m.stderr = ""
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "diff" in cmd_str and "--quiet" in cmd_str:
            m.returncode = 0
        elif "--check" in cmd_str:
            m.returncode = 0
        elif "apply" in cmd_str and "-R" not in cmd_str and "--check" not in cmd_str:
            m.returncode = 0
        elif "pytest" in cmd_str:
            raise subprocess.TimeoutExpired(cmd, 120)
        elif "-R" in cmd_str:
            m.returncode = 0
        else:
            m.returncode = 0
        return m

    with patch("subprocess.run", side_effect=_mock_run):
        result = apply_bugfix_candidate(db, c.id)

    assert result.status == "rolled_back"
    assert "test_timeout" in result.failure_reason
    db.refresh(c)
    assert c.status == "rolled_back"
