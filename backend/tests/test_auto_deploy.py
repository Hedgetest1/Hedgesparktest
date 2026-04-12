"""Tests for M3 — auto-deploy after merge.

Subprocess + Redis fully mocked. Verifies the safety stack short-circuits
correctly at every gate, the kill-switch works, idempotency holds, and
ops_alerts are written for every outcome.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services import promotion_pipeline as pp


def _fake_promo(promo_id: int = 1, status: str = "merged"):
    p = MagicMock()
    p.id = promo_id
    p.status = status
    p.merge_commit_sha = "abc123def4567890"
    p.branch_name = "autofix/promo-1"
    p.bugfix_candidate_id = 99
    p.merged_at = datetime.now()
    return p


def _fake_db(promos: list):
    db = MagicMock()
    chain = db.query.return_value.filter.return_value.order_by.return_value.limit.return_value
    chain.all.return_value = promos
    db.commit = MagicMock()
    db.rollback = MagicMock()
    return db


def _reset_state():
    pp._auto_deploy_last = None


def test_kill_switch_blocks_all_deploys(monkeypatch):
    _reset_state()
    monkeypatch.setenv("AUTO_DEPLOY_PAUSED", "1")
    db = _fake_db([_fake_promo()])
    summary = pp.run_auto_deploy(db)
    assert summary["skipped_disabled"] == 1
    assert summary["deployed"] == 0


def test_cooldown_blocks_consecutive_deploys(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    pp._mark_auto_deploy_done()
    db = _fake_db([_fake_promo()])
    summary = pp.run_auto_deploy(db)
    assert summary["skipped_cooldown"] == 1


def test_already_deployed_promotion_skipped(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=42)])
    with patch.object(pp, "_is_promotion_already_deployed", return_value=True):
        summary = pp.run_auto_deploy(db)
    assert summary["skipped_already_deployed"] == 1
    assert summary["deployed"] == 0


def test_preflight_failure_aborts_and_writes_alert(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=10)])
    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_shell", return_value=(1, "preflight rejected: health critical")), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)
    assert summary["deployed"] == 0
    assert summary["failed"] == 1
    result = summary["results"][0]
    assert result["status"] == "preflight_blocked"
    # Only one shell call (the preflight) — git pull never reached
    assert mock_alert.called
    assert mock_alert.call_args.kwargs["alert_type"] == "deploy_failed"


def test_git_pull_failure_writes_critical_alert(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=11)])
    shell_calls: list[list[str]] = []

    def _fake_shell(cmd, **kw):
        shell_calls.append(cmd)
        if "deploy_gate.py" in cmd[1] and "--preflight" in cmd:
            return (0, "preflight ok")
        if cmd[0] == "git":
            return (1, "merge conflict in app/services/foo.py")
        return (0, "")

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_shell", side_effect=_fake_shell), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)

    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "git_pull_failed"
    assert mock_alert.call_args.kwargs["severity"] == "critical"
    assert any(c[0] == "git" for c in shell_calls)


def test_pm2_failure_writes_critical_alert(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=12)])

    def _fake_shell(cmd, **kw):
        if "deploy_gate.py" in cmd[1]:
            return (0, "ok")
        if cmd[0] == "git":
            return (0, "Already up to date.")
        if cmd[0] == "pm2":
            return (1, "process not found")
        return (0, "")

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_shell", side_effect=_fake_shell), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)

    assert summary["results"][0]["status"] == "pm2_restart_failed"
    assert mock_alert.call_args.kwargs["severity"] == "critical"


def test_postdeploy_failure_marks_rolled_back(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=13)])

    def _fake_shell(cmd, **kw):
        if "--preflight" in cmd:
            return (0, "ok")
        if cmd[0] == "git":
            return (0, "ok")
        if cmd[0] == "pm2":
            return (0, "ok")
        if "--postdeploy" in cmd:
            return (1, "health failed, rolled back")
        return (0, "")

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_mark_promotion_deployed") as mock_mark, \
         patch.object(pp, "_shell", side_effect=_fake_shell), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)

    assert summary["rolled_back"] == 1
    result = summary["results"][0]
    assert result["status"] == "postdeploy_failed_rolled_back"
    assert result["rolled_back"] is True
    assert mock_alert.call_args.kwargs["alert_type"] == "deploy_rolled_back"
    # Marker still set so we don't loop on the broken promotion
    mock_mark.assert_called_once()


def test_full_success_path_marks_deployed_and_alerts(monkeypatch):
    _reset_state()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_fake_promo(promo_id=14)])

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_mark_promotion_deployed") as mock_mark, \
         patch.object(pp, "_shell", return_value=(0, "ok")), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)

    assert summary["deployed"] == 1
    assert summary["failed"] == 0
    assert summary["rolled_back"] == 0
    assert summary["results"][0]["status"] == "deployed"
    mock_mark.assert_called_once()
    success_alerts = [
        c for c in mock_alert.call_args_list
        if c.kwargs.get("alert_type") == "deploy_succeeded"
    ]
    assert len(success_alerts) == 1
    assert success_alerts[0].kwargs["severity"] == "info"


def test_default_auto_merge_enabled():
    """The flip from default-OFF to default-ON for auto-merge."""
    import os
    saved = os.environ.pop("AUTO_MERGE_TIER0", None)
    try:
        assert pp._is_auto_merge_enabled() is True
    finally:
        if saved is not None:
            os.environ["AUTO_MERGE_TIER0"] = saved


def test_auto_merge_kill_switch_zero_disables():
    import os
    saved = os.environ.get("AUTO_MERGE_TIER0")
    os.environ["AUTO_MERGE_TIER0"] = "0"
    try:
        assert pp._is_auto_merge_enabled() is False
    finally:
        if saved is None:
            os.environ.pop("AUTO_MERGE_TIER0", None)
        else:
            os.environ["AUTO_MERGE_TIER0"] = saved
