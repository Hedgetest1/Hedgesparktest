"""Tests for M1+M2 — enriched daily digest with no approve/apply buttons.

Locks the new shape: TIER_0/1 candidates do NOT generate Telegram
buttons; the digest shows 24h pipeline activity, deploy events, RARS
delta, top fixes, and recurring alerts.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services import telegram_agent as ta


def test_digest_returns_string_with_header():
    """Smoke: build runs against a fully-mocked DB without crashing."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0
    msg = ta.build_daily_digest(db)
    assert "*Daily Digest*" in msg
    assert "HedgeSpark" in msg
    assert "running autonomously" in msg.lower()


def test_digest_no_approve_buttons_for_tier0_or_tier1():
    """The whole point of M1: zero TIER_0/1 buttons in the cache."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    db.execute.return_value.scalar.return_value = 0

    ta._digest_buttons_cache.clear()
    ta.build_daily_digest(db)

    # Buttons cache must be empty when no TIER_2 is pending
    flat = [b for row in ta._digest_buttons_cache for b in row]
    callback_data = [b.get("callback_data", "") for b in flat]
    assert not any("/bugfix_approve" in c for c in callback_data)
    assert not any("/bugfix_apply" in c for c in callback_data)
    assert not any("/approve" in c for c in callback_data)


def test_digest_includes_pipeline_24h_section():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.fetchall.return_value = []
    # Make every COUNT scalar return a non-zero number so the section renders
    db.execute.return_value.scalar.return_value = 5
    msg = ta.build_daily_digest(db)
    assert "Pipeline 24h" in msg
    assert "applied" in msg
    assert "rolled_back" in msg
    assert "TIER" in msg and "blocked" in msg


def test_digest_renders_tier2_review_section_when_present():
    """A TIER_2 candidate must produce a `TIER_2 review needed` block
    pointing to the dashboard, NOT a Telegram button."""
    db = MagicMock()

    # First fetchone returns the merchants row, scalars return 0
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.scalar.return_value = 0

    # fetchall side effects:
    # We need the call that fetches TIER_2 pending to return a row.
    # The code calls fetchall on multiple queries — return [] for most
    # and a single tier2 row when the SELECT contains 'patch_risk_tier = 2'.
    real_execute = db.execute

    def _execute(sql, *args, **kwargs):
        result = MagicMock()
        sql_str = str(sql).lower() if hasattr(sql, "__str__") else ""
        if "patch_risk_tier = 2" in sql_str and "patch_proposed" in sql_str:
            row = MagicMock()
            row.__getitem__ = lambda self, idx: {0: 999, 1: "Sensitive auth fix"}[idx]
            result.fetchall.return_value = [row]
        else:
            result.fetchall.return_value = []
        result.fetchone.return_value = (0, 0)
        result.scalar.return_value = 0
        return result

    db.execute.side_effect = _execute
    msg = ta.build_daily_digest(db)
    assert "TIER" in msg and "review needed" in msg
    assert "999" in msg
    assert "dashboard" in msg.lower()
    # Still no Telegram action button — the user must click the dashboard link
    flat = [b for row in ta._digest_buttons_cache for b in row]
    assert not any("999" in str(b.get("callback_data", "")) for b in flat)


def test_digest_deploy_events_section(monkeypatch):
    """Deploy 24h counts must render when ops_alerts contain them."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = (0, 0)
    db.execute.return_value.scalar.return_value = 0

    def _execute(sql, *args, **kwargs):
        result = MagicMock()
        sql_str = str(sql).lower()
        if "deploy_succeeded" in sql_str and "group by" in sql_str:
            result.fetchall.return_value = [
                ("deploy_succeeded", 3),
                ("deploy_rolled_back", 1),
            ]
        else:
            result.fetchall.return_value = []
        result.fetchone.return_value = (0, 0)
        result.scalar.return_value = 0
        return result

    db.execute.side_effect = _execute
    msg = ta.build_daily_digest(db)
    assert "Deploys 24h" in msg
    assert "ok 3" in msg
    assert "rolled_back 1" in msg
