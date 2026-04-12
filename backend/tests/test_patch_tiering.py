"""Tests for patch risk tiering + auto-apply."""
import json
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import text

from app.models.bugfix_candidate import BugFixCandidate
from app.services.bugfix_pipeline import (
    classify_patch_risk,
    run_auto_apply,
    PATCH_TIER_0,
    PATCH_TIER_1,
    PATCH_TIER_2,
)

_OP_KEY = os.environ.get("DASHBOARD_API_KEY", "test-operator-key-for-ci")
_BACKEND_DIR = "/opt/wishspark/backend"


def _op_headers():
    return {"X-API-Key": _OP_KEY, "Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def test_test_only_patch_is_tier_0():
    """Patch touching only tests/ → TIER_0."""
    files = json.dumps(["tests/test_new.py"])
    diff = "+def test_foo():\n+    assert True\n"
    tier, reasons = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_0


def test_safe_service_patch_is_tier_0():
    """Patch touching safe service file → TIER_0."""
    files = json.dumps(["app/services/signal_text.py"])
    diff = '-    return "old"\n+    return "new"\n'
    tier, _ = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_0


def test_forbidden_path_is_tier_2():
    """Patch touching token_crypto → TIER_2."""
    files = json.dumps(["app/core/token_crypto.py"])
    diff = "+change\n"
    tier, _ = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_2


def test_billing_path_is_tier_2():
    """Patch touching billing → TIER_2."""
    files = json.dumps(["app/api/billing.py"])
    tier, _ = classify_patch_risk(files, "+x\n")
    assert tier == PATCH_TIER_2


def test_migrations_is_tier_2():
    """Patch touching migrations → TIER_2."""
    files = json.dumps(["migrations/versions/new.py"])
    tier, _ = classify_patch_risk(files, "+x\n")
    assert tier == PATCH_TIER_2


def test_subprocess_in_diff_is_tier_2():
    """Diff containing subprocess → TIER_2."""
    files = json.dumps(["app/services/signal_text.py"])
    diff = "+import subprocess\n+subprocess.run(['rm', '-rf', '/'])\n"
    tier, reasons = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_2
    assert any("dangerous" in r for r in reasons)


def test_mixed_paths_is_tier_1():
    """Patch touching safe + non-safe (but not forbidden) → TIER_1."""
    files = json.dumps(["app/services/signal_text.py", "app/api/dashboard.py"])
    diff = "+x\n-y\n"
    tier, _ = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_1


def test_large_diff_is_tier_1():
    """Diff > 120 lines → TIER_1 even if all safe paths."""
    files = json.dumps(["tests/test_big.py"])
    diff = "\n".join([f"+line_{i}" for i in range(150)])
    tier, reasons = classify_patch_risk(files, diff)
    assert tier == PATCH_TIER_1
    assert any("large" in r for r in reasons)


def test_no_data_is_tier_1():
    """No patch data → TIER_1."""
    tier, _ = classify_patch_risk(None, None)
    assert tier == PATCH_TIER_1


# ---------------------------------------------------------------------------
# Auto-apply
# ---------------------------------------------------------------------------

def test_auto_apply_tier_0_candidate(db):
    """TIER_0 candidate gets auto-approved + auto-applied."""
    from tests.conftest import make_git_safe_subprocess_mock
    c = BugFixCandidate(
        source_type="manual", source_ref="auto_apply_test",
        title="Safe fix", status="patch_proposed",
        patch_diff="--- a\n+++ b\n",
        patch_files=json.dumps(["tests/test_new.py"]),
        test_command=f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/test_encryption.py -q",
        patch_risk_tier=PATCH_TIER_0,
        priority_score=100,  # win the run_auto_apply ORDER BY vs pre-committed dev DB rows
    )
    db.add(c)
    db.flush()

    with patch("subprocess.run", side_effect=make_git_safe_subprocess_mock()), \
         patch("httpx.get", return_value=MagicMock(status_code=200)):
        summary = run_auto_apply(db)

    db.refresh(c)
    assert c.status == "applied", f"expected applied, got {c.status} (summary={summary})"
    assert c.decided_by == "auto_tier_0"


def test_auto_apply_skips_tier_1(db):
    """TIER_1 candidate NOT auto-applied.

    Scope check via id: we assert on THIS candidate's state, not on the
    summary totals (which reflect whatever's in the shared dev DB).
    """
    c = BugFixCandidate(
        source_type="manual", source_ref="tier1_skip",
        title="Needs human", status="patch_proposed",
        patch_diff="d", patch_files=json.dumps(["app/api/dashboard.py"]),
        patch_risk_tier=PATCH_TIER_1,
        priority_score=100,
    )
    db.add(c)
    db.flush()

    run_auto_apply(db)
    db.refresh(c)
    assert c.status == "patch_proposed"  # unchanged — TIER_1 never auto-applied


def test_auto_apply_skips_tier_2(db):
    """TIER_2 candidate NOT auto-applied.

    Scope check via id: we assert on THIS candidate's state, not on
    summary totals.
    """
    c = BugFixCandidate(
        source_type="manual", source_ref="tier2_skip",
        title="Forbidden", status="patch_proposed",
        patch_diff="d", patch_files=json.dumps(["app/core/deps.py"]),
        patch_risk_tier=PATCH_TIER_2,
        priority_score=100,
    )
    db.add(c)
    db.flush()

    run_auto_apply(db)
    db.refresh(c)
    assert c.status == "patch_proposed"  # TIER_2 never auto-applied


def test_auto_apply_max_per_cycle(db):
    """Max 1 auto-apply per cycle."""
    from tests.conftest import make_git_safe_subprocess_mock
    for i in range(3):
        db.add(BugFixCandidate(
            source_type="manual", source_ref=f"max_test_{i}",
            title=f"Auto {i}", status="patch_proposed",
            patch_diff="d", patch_files=json.dumps(["tests/test.py"]),
            test_command=f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/test_encryption.py -q",
            patch_risk_tier=PATCH_TIER_0,
            priority_score=100 - i,  # ensure deterministic order win
        ))
    db.flush()

    with patch("subprocess.run", side_effect=make_git_safe_subprocess_mock()), \
         patch("httpx.get", return_value=MagicMock(status_code=200)):
        summary = run_auto_apply(db, max_per_cycle=1)

    assert summary["applied"] == 1


def test_auto_apply_failure_stops_cycle(db):
    """Failed auto-apply stops further attempts."""
    from tests.conftest import make_git_safe_subprocess_mock
    c = BugFixCandidate(
        source_type="manual", source_ref="fail_stop",
        title="Will fail", status="patch_proposed",
        patch_diff="d", patch_files=json.dumps(["tests/test.py"]),
        patch_risk_tier=PATCH_TIER_0,
        priority_score=100,  # win priority queue so this is the one picked
    )
    db.add(c)
    db.flush()

    # Simulate dirty tree → apply must fail
    with patch(
        "subprocess.run",
        side_effect=make_git_safe_subprocess_mock(tree_dirty=True),
    ):
        run_auto_apply(db)

    db.refresh(c)
    assert c.status in ("apply_failed", "rolled_back", "patch_proposed")
    assert c.failure_reason and "dirty" in c.failure_reason.lower()


def test_auto_apply_writes_audit(db):
    """Auto-approval writes audit_log with actor=auto_apply."""
    c = BugFixCandidate(
        source_type="manual", source_ref="audit_test",
        title="Audit", status="patch_proposed",
        patch_diff="d", patch_files=json.dumps(["tests/test.py"]),
        test_command=f"{_BACKEND_DIR}/venv/bin/python -m pytest tests/test_encryption.py -q",
        patch_risk_tier=PATCH_TIER_0,
    )
    db.add(c)
    db.flush()

    def _mock_run(cmd, **kwargs):
        return MagicMock(stdout="sha", stderr="", returncode=0)

    with patch("subprocess.run", side_effect=_mock_run), \
         patch("httpx.get", return_value=MagicMock(status_code=200)):
        run_auto_apply(db)

    audit = db.execute(text(
        "SELECT action_type, actor_name FROM audit_log WHERE action_type = 'bugfix_auto_approved' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    assert audit is not None
    assert audit[1] == "auto_apply"


# ---------------------------------------------------------------------------
# API exposure
# ---------------------------------------------------------------------------

def test_list_exposes_tier(client, db):
    """GET /ops/bugfixes includes patch_risk_tier."""
    db.add(BugFixCandidate(
        source_type="manual", source_ref="api_tier",
        title="Tier API", status="patch_proposed",
        patch_risk_tier=PATCH_TIER_0,
    ))
    db.flush()
    db.commit()

    resp = client.get("/ops/bugfixes", headers=_op_headers())
    assert resp.status_code == 200
    data = resp.json()
    tier_entry = [c for c in data if c["title"] == "Tier API"]
    assert len(tier_entry) >= 1
    assert tier_entry[0]["patch_risk_tier"] == 0
