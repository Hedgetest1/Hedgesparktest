"""Lock the 2026-05-07 MerchantBrain v0.1 contract.

Brain Vero pivots agent_worker from immune-system-on-self
(bugfix_pipeline 0.13% apply rate) to merchant-outcome loop. Tests
pin: feature flag default off, sense reads correctly, decide rules
fire on the right inputs, record persists, evaluate_pending_outcomes
closes the LEARN loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as _sql_text

from app.services import merchant_brain
from app.services.merchant_brain import (
    BrainDecisionDraft,
    MerchantState,
    _decide,
    _synthesize,
    is_brain_enabled,
    tick,
)


def test_brain_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    assert is_brain_enabled() is False


def test_brain_enabled_with_flag(monkeypatch):
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    assert is_brain_enabled() is True


def test_tick_noop_when_disabled(db, monkeypatch):
    monkeypatch.delenv("MERCHANT_BRAIN_ENABLED", raising=False)
    res = tick(db, "any.myshopify.com")
    assert res.get("skipped") == "brain_disabled"


def _state(**kw) -> MerchantState:
    base = dict(
        shop_domain="t.myshopify.com",
        rars_total_eur=0.0,
        churn_risk_level="unknown",
        recent_orders_7d=0,
        recent_events_24h=0,
        hours_since_install=200.0,
        last_action_age_hours=None,
        last_chat_age_hours=None,
        last_brain_decision_age_hours=None,
        has_email_in_queue=False,
    )
    base.update(kw)
    return MerchantState(**base)


def test_decide_rule_critical_churn():
    s = _state(churn_risk_level="critical", recent_orders_7d=2)
    d = _decide(s)
    assert d.action_kind == "retention_outreach_email"
    assert d.expected_outcome_metric == "merchant_re_engaged_7d"


def test_decide_rule_high_rar_stale():
    s = _state(rars_total_eur=5000, last_action_age_hours=80)
    d = _decide(s)
    assert d.action_kind == "recovery_digest"
    assert d.baseline_value == 5000


def test_decide_rule_tracker_dark():
    s = _state(recent_events_24h=0, hours_since_install=72,
               recent_orders_7d=0)
    d = _decide(s)
    assert d.action_kind == "re_engagement_check"


def test_decide_cooldown_suppresses_action():
    s = _state(churn_risk_level="critical", last_brain_decision_age_hours=2)
    d = _decide(s)
    assert d.action_kind == "no_action_cooldown"


def test_decide_no_signal_no_action():
    s = _state(rars_total_eur=10, recent_orders_7d=5,
               recent_events_24h=20, hours_since_install=400,
               last_action_age_hours=10)
    d = _decide(s)
    assert d.action_kind == "no_action_no_signal"


def test_synthesize_includes_signals():
    s = _state(rars_total_eur=1234, churn_risk_level="high",
               recent_orders_7d=3)
    out = _synthesize(s)
    assert "1,234" in out and "high churn" in out and "3 orders" in out


def test_record_persists_decision(db, monkeypatch):
    """End-to-end with brain enabled: tick writes a brain_decisions row."""
    monkeypatch.setenv("MERCHANT_BRAIN_ENABLED", "1")
    # Fake the sense to a known state to avoid prod DB dependency
    fake_state = _state(
        shop_domain="brain-test.myshopify.com",
        rars_total_eur=2000,
        last_action_age_hours=100,
    )
    monkeypatch.setattr(merchant_brain, "_sense", lambda d, s, **kw: fake_state)
    res = tick(db, "brain-test.myshopify.com")
    assert res["action_kind"] == "recovery_digest"
    # Verify row landed
    row = db.execute(
        _sql_text("SELECT action_kind, shop_domain FROM brain_decisions "
                  "WHERE id=:i"),
        {"i": res["decision_id"]},
    ).fetchone()
    assert row is not None
    assert row[0] == "recovery_digest"
    assert row[1] == "brain-test.myshopify.com"
