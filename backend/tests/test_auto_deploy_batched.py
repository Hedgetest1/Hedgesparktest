"""Tests for C4 — batched auto-deploy window."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services import promotion_pipeline as pp


def _promo(promo_id: int, sha: str = "abc1234567"):
    p = MagicMock()
    p.id = promo_id
    p.status = "merged"
    p.merge_commit_sha = sha
    p.branch_name = f"autofix/promo-{promo_id}"
    p.bugfix_candidate_id = 100 + promo_id
    p.merged_at = datetime.now() - timedelta(minutes=promo_id)
    return p


def _fake_db(promos):
    db = MagicMock()
    chain = db.query.return_value.filter.return_value.order_by.return_value.limit.return_value
    chain.all.return_value = promos
    db.commit = MagicMock()
    db.rollback = MagicMock()
    return db


def _reset():
    pp._auto_deploy_last = None


def test_batch_of_three_promos_uses_single_pm2_restart(monkeypatch):
    """The whole point: 3 fixes → 1 git pull + 1 pm2 restart, not 3 of each."""
    _reset()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_promo(1), _promo(2), _promo(3)])

    shell_calls: list[list[str]] = []

    def _fake_shell(cmd, **kw):
        shell_calls.append(cmd)
        return (0, "ok")

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_mark_promotion_deployed") as mock_mark, \
         patch.object(pp, "_shell", side_effect=_fake_shell), \
         patch("app.services.alerting.write_alert"):
        summary = pp.run_auto_deploy(db)

    assert summary["batch_size"] == 3
    assert summary["deployed"] == 3
    assert summary["failed"] == 0

    # Exactly 1 preflight, 1 git pull, 1 pm2 restart, 1 postdeploy
    pm2_calls = [c for c in shell_calls if c[0] == "pm2"]
    git_calls = [c for c in shell_calls if c[0] == "git"]
    preflight_calls = [c for c in shell_calls if "--preflight" in c]
    postdeploy_calls = [c for c in shell_calls if "--postdeploy" in c]

    assert len(pm2_calls) == 1
    assert len(git_calls) == 1
    assert len(preflight_calls) == 1
    assert len(postdeploy_calls) == 1

    # Every promo got its idempotency marker
    assert mock_mark.call_count == 3


def test_batch_postdeploy_failure_marks_all_rolled_back(monkeypatch):
    """If postdeploy fails, the WHOLE batch is rolled back atomically."""
    _reset()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    db = _fake_db([_promo(10), _promo(11)])

    def _fake_shell(cmd, **kw):
        if "--postdeploy" in cmd:
            return (1, "health check failed")
        return (0, "ok")

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_mark_promotion_deployed") as mock_mark, \
         patch.object(pp, "_shell", side_effect=_fake_shell), \
         patch("app.services.alerting.write_alert") as mock_alert:
        summary = pp.run_auto_deploy(db)

    assert summary["rolled_back"] == 2  # both promotions rolled back
    assert summary["deployed"] == 0
    # All batched promotions get the marker so we don't loop on the broken batch
    assert mock_mark.call_count == 2

    rollback_alerts = [
        c for c in mock_alert.call_args_list
        if c.kwargs.get("alert_type") == "deploy_rolled_back"
    ]
    assert len(rollback_alerts) == 1  # ONE alert for the whole batch


def test_batch_capped_at_max_size(monkeypatch):
    """Even if 10 promotions are pending, the batch never exceeds the cap."""
    _reset()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    promos = [_promo(i) for i in range(10)]
    db = _fake_db(promos)

    with patch.object(pp, "_is_promotion_already_deployed", return_value=False), \
         patch.object(pp, "_mark_promotion_deployed"), \
         patch.object(pp, "_shell", return_value=(0, "ok")), \
         patch("app.services.alerting.write_alert"):
        summary = pp.run_auto_deploy(db)

    assert summary["batch_size"] <= pp._AUTO_DEPLOY_BATCH_MAX_SIZE
    assert summary["deployed"] == summary["batch_size"]


def test_batch_skips_already_deployed_promotions(monkeypatch):
    _reset()
    monkeypatch.delenv("AUTO_DEPLOY_PAUSED", raising=False)
    promos = [_promo(20), _promo(21), _promo(22)]
    db = _fake_db(promos)

    # Promo 21 is already deployed
    def _is_deployed(promo_id):
        return promo_id == 21

    with patch.object(pp, "_is_promotion_already_deployed", side_effect=_is_deployed), \
         patch.object(pp, "_mark_promotion_deployed"), \
         patch.object(pp, "_shell", return_value=(0, "ok")), \
         patch("app.services.alerting.write_alert"):
        summary = pp.run_auto_deploy(db)

    assert summary["skipped_already_deployed"] == 1
    assert summary["batch_size"] == 2
    assert summary["deployed"] == 2
