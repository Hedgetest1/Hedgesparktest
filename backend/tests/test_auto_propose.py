"""Tests for auto-propose, proposal metadata, and git commit on apply."""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    run_auto_propose,
    apply_bugfix_candidate,
    _git_commit_patch,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
_BACKEND_DIR = "/opt/wishspark/backend"


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_open(db, title="Test bug"):
    # Clear pre-existing open candidates so they don't interfere with test
    db.query(BugFixCandidate).filter(
        BugFixCandidate.status.in_(["open", "analyzed"]),
        BugFixCandidate.proposal_attempted_at.is_(None),
    ).update({"proposal_attempted_at": _now()}, synchronize_session="fetch")
    db.flush()

    c = BugFixCandidate(
        source_type="manual", source_ref=f"auto_{title}",
        title=title, summary="something broke", status="open",
    )
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Auto-propose
# ---------------------------------------------------------------------------

def test_auto_propose_attempts_open_candidates(db):
    """run_auto_propose attempts proposal for open candidates."""
    c = _make_open(db)
    assert c.proposal_attempted_at is None

    mock_response = json.dumps({
        "patch_summary": "Fix it",
        "files": ["tests/test_mock_propose.py"],
        "diff": "--- /dev/null\n+++ b/tests/test_mock_propose.py\n@@ -0,0 +1 @@\n+# test\n",
        "test_command": "pytest",
    })

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "anthropic", "claude-sonnet-4-6"),
    ):
        summary = run_auto_propose(db)

    assert summary["attempted"] >= 1
    assert summary["proposed"] >= 1
    db.refresh(c)
    assert c.proposal_attempted_at is not None
    assert c.status == "patch_proposed"


def test_auto_propose_skips_already_attempted(db):
    """Candidates with proposal_attempted_at set are skipped."""
    c = _make_open(db)
    c.proposal_attempted_at = _now()
    db.flush()

    summary = run_auto_propose(db)
    # Should not attempt this one (already attempted)
    assert summary["attempted"] == 0 or c.status == "open"


def test_auto_propose_stores_error_on_failure(db):
    """LLM failure stores proposal_error without crashing."""
    c = _make_open(db)

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=("", "anthropic", "claude-sonnet-4-6"),
    ):
        summary = run_auto_propose(db)

    assert summary["failed"] >= 1
    db.refresh(c)
    assert c.proposal_attempted_at is not None
    assert c.proposal_error is not None


def test_auto_propose_does_not_crash_worker(db):
    """Exception during proposal does not crash."""
    c = _make_open(db)

    with patch("app.services.bugfix_pipeline._call_llm", side_effect=Exception("LLM down")):
        summary = run_auto_propose(db)

    assert summary["failed"] >= 1
    db.refresh(c)
    assert "LLM down" in (c.proposal_error or "")


def test_auto_propose_sets_provider(db):
    """Successful proposal records the LLM provider."""
    c = _make_open(db)
    mock_response = json.dumps({"patch_summary": "fix", "files": ["tests/test_mock_prov.py"], "diff": "--- /dev/null\n+++ b/tests/test_mock_prov.py\n@@ -0,0 +1 @@\n+# test\n", "test_command": ""})

    with patch(
        "app.services.bugfix_pipeline._call_llm",
        return_value=(mock_response, "openai", "gpt-4o-mini"),
    ):
        run_auto_propose(db)

    db.refresh(c)
    # proposal_provider now reflects the ACTUAL provider the router used
    # (set inside propose_patch from the _call_llm tuple), not an env-based
    # heuristic. See bugfix_pipeline.py 2026-04-23 fix.
    assert c.proposal_provider == "openai"


# ---------------------------------------------------------------------------
# Git commit on apply
# ---------------------------------------------------------------------------

def test_successful_apply_creates_commit(db):
    """Successful apply path calls git commit and stores SHA."""
    from tests.conftest import make_git_safe_subprocess_mock
    c = BugFixCandidate(
        source_type="manual", source_ref="commit_test",
        title="Commit test", status="approved",
        patch_diff="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new",
        patch_files=json.dumps(["app/services/test_file.py"]),
        test_command=f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/test_encryption.py -q",
    )
    db.add(c)
    db.flush()

    mock_health = MagicMock(status_code=200)

    # Mock the diff --cached --name-only check so our targeted git add
    # verification sees exactly the files we expect (the patch's file list).
    def _git_safe(cmd, **kw):
        base = make_git_safe_subprocess_mock(commit_sha="abc123def456")
        m = base(cmd, **kw)
        if len(cmd) >= 4 and cmd[:4] == ["git", "diff", "--cached", "--name-only"]:
            m.stdout = "app/services/test_file.py\n"
        return m

    with patch("subprocess.run", side_effect=_git_safe), \
         patch("httpx.get", return_value=mock_health):
        result = apply_bugfix_candidate(db, c.id)

    assert result.status == "applied"
    db.refresh(c)
    assert c.git_commit_sha == "abc123def456"


def test_git_commit_failure_causes_rollback(db):
    """Git commit failure → rolled_back."""
    c = BugFixCandidate(
        source_type="manual", source_ref="commit_fail_test",
        title="Commit fail", status="approved",
        patch_diff="--- a\n+++ b\n",
        patch_files=json.dumps(["app/services/test.py"]),
        test_command=f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/test_encryption.py -q",
    )
    db.add(c)
    db.flush()

    call_history = []
    def _mock_run(cmd, **kwargs):
        call_history.append(cmd)
        m = MagicMock()
        m.stdout = ""
        m.stderr = ""
        m.returncode = 0
        if "commit" in cmd:
            m.returncode = 1  # commit fails
            m.stderr = "nothing to commit"
        elif "pytest" in " ".join(cmd):
            m.returncode = 0
        return m

    mock_health = MagicMock(status_code=200)

    with patch("subprocess.run", side_effect=_mock_run), \
         patch("httpx.get", return_value=mock_health):
        result = apply_bugfix_candidate(db, c.id)

    assert result.status == "rolled_back"
    assert "commit" in (result.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# API detail exposes new fields
# ---------------------------------------------------------------------------

def test_detail_exposes_proposal_metadata(client, db):
    """GET detail includes proposal_attempted_at, proposal_error, git_commit_sha."""
    c = BugFixCandidate(
        source_type="manual", source_ref="meta_test",
        title="Meta test", status="patch_proposed",
        proposal_attempted_at=_now(),
        proposal_provider="openai",
        proposal_error=None,
        git_commit_sha="abc123",
    )
    db.add(c)
    db.flush()
    db.commit()

    resp = client.get(f"/ops/bugfixes/{c.id}", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["proposal_attempted_at"] is not None
    assert data["proposal_provider"] == "openai"
    assert data["git_commit_sha"] == "abc123"


# ---------------------------------------------------------------------------
# Worker integration
# ---------------------------------------------------------------------------

def test_agent_worker_has_auto_propose():
    """agent_worker _run_bug_triage calls run_auto_propose."""
    import inspect
    from app.workers.agent_worker import _run_bug_triage
    source = inspect.getsource(_run_bug_triage)
    assert "run_auto_propose" in source
