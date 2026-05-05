"""Tests for H6 — onboarding drift detector (stalled first-7-days installs)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.onboarding_health import (
    check_onboarding_health,
    detect_drifting_new_installs,
    write_onboarding_alerts,
)


def _mock_row(shop: str, hours_ago: int, nudge_count: int = 0):
    r = MagicMock()
    r.shop_domain = shop
    r.installed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago)
    r.nudge_count = nudge_count
    return r


def test_detect_drift_returns_empty_when_no_rows():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    assert detect_drifting_new_installs(db) == []


def test_detect_drift_flags_disengaged_install():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _mock_row("drifter.myshopify.com", hours_ago=72, nudge_count=0),
    ]
    with patch("app.services.goals.get_goals", return_value=[]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[]):
        result = detect_drifting_new_installs(db)
    assert len(result) == 1
    assert result[0]["shop_domain"] == "drifter.myshopify.com"
    assert result[0]["hours_since_install"] == 72
    assert result[0]["goals_set"] == 0
    assert result[0]["webhooks_configured"] == 0


def test_detect_drift_skips_engaged_install_with_goals():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _mock_row("engaged.myshopify.com", hours_ago=48, nudge_count=0),
    ]
    with patch("app.services.goals.get_goals", return_value=[{"id": "g1"}]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[]):
        result = detect_drifting_new_installs(db)
    assert result == []


def test_detect_drift_skips_install_with_active_nudge():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _mock_row("nudged.myshopify.com", hours_ago=48, nudge_count=2),
    ]
    with patch("app.services.goals.get_goals", return_value=[]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[]):
        result = detect_drifting_new_installs(db)
    assert result == []


def test_detect_drift_skips_install_with_webhook():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [
        _mock_row("wired.myshopify.com", hours_ago=48, nudge_count=0),
    ]
    with patch("app.services.goals.get_goals", return_value=[]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[{"id": "w1"}]):
        result = detect_drifting_new_installs(db)
    assert result == []


def test_check_onboarding_health_includes_drift_count():
    db = MagicMock()
    execute_results = [
        MagicMock(fetchall=MagicMock(return_value=[])),  # stuck
        MagicMock(fetchall=MagicMock(return_value=[])),  # pixel_abandon
        MagicMock(fetchall=MagicMock(return_value=[])),  # slow_activation
        MagicMock(fetchall=MagicMock(return_value=[     # drift
            _mock_row("a.myshopify.com", hours_ago=48),
        ])),
        MagicMock(scalar=MagicMock(return_value=10)),  # total
        MagicMock(scalar=MagicMock(return_value=8)),   # ready
    ]
    db.execute.side_effect = execute_results
    with patch("app.services.goals.get_goals", return_value=[]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[]):
        health = check_onboarding_health(db)
    assert health["drifting_new_installs"] == 1
    assert len(health["drift_details"]) == 1
    assert health["healthy"] is False


def test_write_onboarding_alerts_emits_drift_alert():
    db = MagicMock()
    # 4 queries in order: stuck, pixel_abandon, slow, drift
    execute_results = [
        MagicMock(fetchall=MagicMock(return_value=[])),
        MagicMock(fetchall=MagicMock(return_value=[])),
        MagicMock(fetchall=MagicMock(return_value=[])),
        MagicMock(fetchall=MagicMock(return_value=[
            _mock_row("drift1.myshopify.com", hours_ago=60),
        ])),
    ]
    db.execute.side_effect = execute_results

    with patch("app.services.alerting.write_alert") as mock_alert, \
         patch("app.services.alerting.heal_per_shop_alerts") as _mock_heal, \
         patch("app.services.goals.get_goals", return_value=[]), \
         patch("app.services.signal_webhooks.list_webhooks", return_value=[]):
        result = write_onboarding_alerts(db)

    assert result["drifting_new_installs"] == 1
    drift_calls = [c for c in mock_alert.call_args_list if c.kwargs.get("alert_type") == "onboarding_drift"]
    assert len(drift_calls) == 1
    assert drift_calls[0].kwargs["shop_domain"] == "drift1.myshopify.com"
