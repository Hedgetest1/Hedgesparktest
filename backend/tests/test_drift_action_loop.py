"""Tests for A2 — onboarding drift action loop.

Locks the contract: detected drifters get a re-engagement email via
email_orchestrator (not bypass), per-shop cooldown holds, episode
counter advances, and after MAX_EPISODES the operator is escalated.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import onboarding_health as oh


def _drifter(shop="drift-shop.myshopify.com", hours=72):
    return {
        "shop_domain": shop,
        "installed_at": "2026-04-08T12:00:00",
        "hours_since_install": hours,
        "active_nudges": 0,
        "goals_set": 0,
        "webhooks_configured": 0,
    }


def _patch_redis_state(allowed=True, episode=0):
    """Patch the cooldown helpers so we control the per-shop state."""
    return patch.object(
        oh, "_can_send_reengagement", return_value=(allowed, episode),
    ), patch.object(
        oh, "_record_reengagement_sent", return_value=episode + 1,
    )


def test_email_template_renders_subject_and_html():
    subject, html, plain = oh._build_reengagement_email(_drifter(hours=120))
    assert "HedgeSpark" in subject or "60 secondi" in subject
    assert "60 second" in html.lower()
    assert "obiettivo" in html.lower()
    assert "https://app.hedgesparkhq.com/app?go=goals" in html
    assert "60 second" in plain.lower()
    # No raw template placeholders
    assert "{shop}" not in html
    assert "{days}" not in html


def test_send_reengagement_full_path_submits_intent():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = ("owner@drift-shop.com",)

    with patch.object(oh, "_can_send_reengagement", return_value=(True, 0)), \
         patch.object(oh, "_record_reengagement_sent", return_value=1), \
         patch("app.services.email_orchestrator.submit_intent") as mock_submit:
        result = oh.send_reengagement_for_drifter(db, _drifter())

    assert result["status"] == "sent"
    assert result["episode"] == 1
    assert mock_submit.called

    intent = mock_submit.call_args.args[1]
    assert intent.email_type == "reengagement_drift"
    assert intent.to_email == "owner@drift-shop.com"
    assert intent.shop_domain == "drift-shop.myshopify.com"
    assert intent.producer == "onboarding_health"


def test_send_reengagement_skipped_on_cooldown():
    db = MagicMock()
    with patch.object(oh, "_can_send_reengagement", return_value=(False, 1)):
        result = oh.send_reengagement_for_drifter(db, _drifter())
    assert result["status"] == "skipped_cooldown"
    assert result["episode"] == 1


def test_send_reengagement_skipped_when_no_email():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (None,)
    with patch.object(oh, "_can_send_reengagement", return_value=(True, 0)):
        result = oh.send_reengagement_for_drifter(db, _drifter())
    assert result["status"] == "skipped_no_email"


def test_max_episodes_escalates_to_operator():
    """After 3 episodes the merchant gets a hands-off escalation
    instead of yet another email."""
    db = MagicMock()
    captured_alerts = []

    def _fake_alert(db, **kwargs):
        captured_alerts.append(kwargs)
        return MagicMock(id=1)

    with patch.object(oh, "_can_send_reengagement", return_value=(True, 3)), \
         patch("app.services.alerting.write_alert", side_effect=_fake_alert), \
         patch("app.services.email_orchestrator.submit_intent") as mock_submit:
        result = oh.send_reengagement_for_drifter(db, _drifter())

    assert result["status"] == "escalated"
    # No further email should have been queued at this point
    assert not mock_submit.called
    assert len(captured_alerts) == 1
    assert captured_alerts[0]["alert_type"] == "drift_chronic_escalation"
    assert captured_alerts[0]["severity"] == "warning"


def test_drift_action_loop_aggregates_results():
    db = MagicMock()
    drifters = [
        _drifter(shop="a.myshopify.com"),
        _drifter(shop="b.myshopify.com"),
        _drifter(shop="c.myshopify.com"),
    ]

    def _send_results(db, drifter):
        if drifter["shop_domain"].startswith("a"):
            return {"status": "sent", "episode": 1}
        if drifter["shop_domain"].startswith("b"):
            return {"status": "skipped_cooldown", "episode": 1}
        return {"status": "escalated", "episode": 3}

    with patch.object(oh, "detect_drifting_new_installs", return_value=drifters), \
         patch.object(oh, "send_reengagement_for_drifter", side_effect=_send_results):
        summary = oh.run_drift_action_loop(db)

    assert summary["drifters"] == 3
    assert summary["sent"] == 1
    assert summary["skipped_cooldown"] == 1
    assert summary["escalated"] == 1
